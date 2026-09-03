# -*- coding: utf-8 -*-
"""討伐クエスト3件（quest_1..3）を1件ずつ生成して結合する。

openai_proxy の `splitRequest` / `handleSplitGeneration` 相当。
ゲームがクエストジェネレータ＋`quest_1`/`quest_2`/`quest_3` 必須スキーマで
一括生成しようとするとき、`send_request` を3回に分け、各回の schema / 文面を
1スロットに絞ってから呼び、結果を結合して返す。

敵数上限そのものは `1133_generation_limits` 側。こちらは分割と
「3つ→1つ」の文言整形だけを持つ。
"""

import threading

from instantale_modloader.llm import watch_aliases

# --------------------------------------------------------------------------
# 設定
# --------------------------------------------------------------------------
ENABLED = True
MAX_ATTEMPTS = 3
LOG_LIMIT = 30

LOG_BASENAME = "quest_split_generation.log"

QUEST_GENERATOR_MARK = "クエストジェネレータです"
QUEST_SLOT_KEYS = ("quest_1", "quest_2", "quest_3")

CLIENT_ENEMY_COUNT_INSTRUCTIONS = (
    "クエスト中に出現するnormal2-3種、boss1種をそれぞれ必ず設定してください。"
    "minibossに関してはクエスト難易度が5以上で1種、59以上で2種を設定してください。",
    "クエスト中に出現するnormal2-3種、miniboss1種、boss1種をそれぞれ必ず設定してください。",
)

REWRITTEN_ENEMY_COUNT_INSTRUCTION = (
    "enemies 配列には種別ごと上限以内だけを入れる。normal は2〜3種（上限3）、"
    "miniboss は最大3種（難易度に応じ1〜2種を目安）、boss は enemies に入れず "
    "boss フィールドへ1種だけ。同一敵の反復・別名水増しは禁止。"
    "上限到達で配列を閉じること。"
)

SINGLE_QUEST_OUTPUT_LIMITS = (
    "【出力上限・終了規則】\n"
    "- enemies 配列は合計2〜6件。typeごと上限: normal 最大3種、miniboss 最大3種。"
    "boss は enemies に入れない。\n"
    "- 上限に達したら直ちに配列を閉じる。同じ敵・別名・強化版・サイズ違いでの"
    "水増しは禁止。\n"
    "- [QUEST_KEY] のクエストを1件完成したら、直ちにJSONを閉じる。"
    "説明文、追加クエスト、続きの出力は禁止する。"
)

QUEST_ENEMIES_MAX_ITEMS = 6

SEND_TARGET = "scripts.llm.llm_manager:send_request"
POST_TARGET = "openai._base_client:SyncAPIClient.post"
CHAT_TARGET = "llama_cpp_runtime_completion:LlamaCppClient.chat"

_slot = threading.local()


def is_quest_generator_messages(messages):
    if not isinstance(messages, (list, tuple)):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and QUEST_GENERATOR_MARK in content:
            return True
    return False


def schema_of(structure):
    if isinstance(structure, dict):
        return structure
    for name in ("model_json_schema", "schema"):
        fn = getattr(structure, name, None)
        if callable(fn):
            try:
                got = fn()
            except Exception:
                got = None
            if isinstance(got, dict):
                return got
    cls = structure if isinstance(structure, type) else type(structure)
    fn = getattr(cls, "model_json_schema", None)
    if callable(fn):
        try:
            got = fn()
            if isinstance(got, dict):
                return got
        except Exception:
            pass
    return None


def has_quest_slots(schema):
    if not isinstance(schema, dict):
        return False
    props = schema.get("properties")
    if not isinstance(props, dict):
        return False
    return all(key in props for key in QUEST_SLOT_KEYS)


def with_enemies_max_items(schema):
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    defs = out.get("$defs") or out.get("definitions")
    if isinstance(defs, dict) and "QuestStructure" in defs:
        quest = defs.get("QuestStructure")
        props = quest.get("properties") if isinstance(quest, dict) else None
        enemies = props.get("enemies") if isinstance(props, dict) else None
        if isinstance(enemies, dict) and enemies.get("type") == "array":
            new_defs = dict(defs)
            new_quest = dict(quest)
            new_props = dict(props)
            new_enemies = dict(enemies)
            new_enemies["maxItems"] = QUEST_ENEMIES_MAX_ITEMS
            new_props["enemies"] = new_enemies
            new_quest["properties"] = new_props
            new_defs["QuestStructure"] = new_quest
            if "$defs" in out:
                out["$defs"] = new_defs
            else:
                out["definitions"] = new_defs
    return out


def single_slot_schema(schema, quest_key):
    if not isinstance(schema, dict) or quest_key not in (schema.get("properties") or {}):
        return None
    props = schema["properties"]
    out = dict(schema)
    out["properties"] = {quest_key: props[quest_key]}
    out["required"] = [quest_key]
    return with_enemies_max_items(out)


def rewrite_enemy_count_instructions(content):
    out = content
    for instruction in CLIENT_ENEMY_COUNT_INSTRUCTIONS:
        if instruction in out:
            out = out.replace(instruction, REWRITTEN_ENEMY_COUNT_INSTRUCTION)
    return out


def rewrite_quest_generator_system(content):
    out = content.replace("3つの【討伐】クエスト", "1つの【討伐】クエスト")
    return rewrite_enemy_count_instructions(out)


def build_single_messages(messages, quest_key):
    """プロキシ buildSingleQuestRequest の messages 整形。"""
    if not isinstance(messages, list):
        return messages
    last_user = -1
    for index, message in enumerate(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            last_user = index
    out = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            out.append(message)
            continue
        content = message["content"]
        role = message.get("role")
        if role == "system" and QUEST_GENERATOR_MARK in content:
            content = rewrite_quest_generator_system(content)
        elif role == "user":
            lines = content.split("\n")
            kept = []
            for line in lines:
                if line.startswith("- quest_") and not line.startswith(
                        "- {}:".format(quest_key)):
                    # quest_1/2/3 行のうち当該以外を落とす
                    if any(line.startswith("- {}:".format(k)) for k in QUEST_SLOT_KEYS):
                        continue
                kept.append(line)
            content = "\n".join(kept)
            limits = ""
            if index == last_user:
                limits = "\n" + SINGLE_QUEST_OUTPUT_LIMITS.replace(
                    "[QUEST_KEY]", quest_key)
            content = (
                content
                + "\n【今回の生成対象】\n- {} だけを生成すること。{}".format(
                    quest_key, limits)
            )
        replacement = dict(message)
        replacement["content"] = content
        out.append(replacement)
    return out


def get_field(value, name):
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def merge_slots(structure, parts):
    """3スロットを元の structure 型へ戻す。ダメなら dict。"""
    payload = {key: parts[key] for key in QUEST_SLOT_KEYS if key in parts}
    cls = structure if isinstance(structure, type) else None
    if cls is None and structure is not None:
        cls = type(structure)
    if cls is not None:
        validate = getattr(cls, "model_validate", None)
        if callable(validate):
            try:
                return validate(payload)
            except Exception:
                pass
        try:
            return cls(**payload)
        except Exception:
            pass
    return payload


def message_arg(args, kwargs):
    if len(args) >= 2 and isinstance(args[1], list):
        return args[1]
    message = kwargs.get("message")
    return message if isinstance(message, list) else None


def structure_arg(args, kwargs):
    if len(args) >= 3:
        return args[2]
    return kwargs.get("structure")


def patch_openai_body(body, quest_key, messages):
    """chat/completions body を1スロット用に書き換えた写しを返す。"""
    if not isinstance(body, dict):
        return None
    rf = body.get("response_format")
    schema = None
    if isinstance(rf, dict) and rf.get("type") == "json_schema":
        js = rf.get("json_schema")
        if isinstance(js, dict):
            schema = js.get("schema")
    if not has_quest_slots(schema):
        return None
    single = single_slot_schema(schema, quest_key)
    if single is None:
        return None
    new_body = dict(body)
    new_body["messages"] = messages
    new_js = dict(rf.get("json_schema") or {})
    new_js["schema"] = single
    new_body["response_format"] = dict(rf, json_schema=new_js)
    return new_body


def apply(ctx):
    if not ENABLED:
        ctx.log("quest split generation: off")
        return

    state = {"splits": 0, "slots": 0}
    write = ctx.logger(LOG_BASENAME)

    # OpenAI 互換経路: 送信直前に schema / messages を1スロット化
    @ctx.wrap(POST_TARGET, required=False, safe=True, alias_scan=False)
    def post(orig, self, path=None, *args, **kwargs):
        slot = getattr(_slot, "key", None)
        messages = getattr(_slot, "messages", None)
        body = kwargs.get("body")
        if slot and messages is not None and isinstance(body, dict):
            patched = patch_openai_body(body, slot, messages)
            if patched is not None:
                kwargs = dict(kwargs, body=patched)
        return orig(self, path, *args, **kwargs)

    # ローカル llama.cpp: format が schema dict のときだけ1スロット化
    @ctx.wrap(CHAT_TARGET, required=False, safe=True)
    def chat(orig, self, model, messages, format=None, *args, **kwargs):
        slot = getattr(_slot, "key", None)
        split_messages = getattr(_slot, "messages", None)
        if slot and split_messages is not None:
            messages = split_messages
            if isinstance(format, dict) and has_quest_slots(format):
                single = single_slot_schema(format, slot)
                if single is not None:
                    format = single
        return orig(self, model, messages, format, *args, **kwargs)

    def install_send(target):
        @ctx.wrap(target, required=False, safe=True)
        def send_request(orig, *args, **kwargs):
            messages = message_arg(args, kwargs)
            structure = structure_arg(args, kwargs)
            schema = schema_of(structure)
            if not (is_quest_generator_messages(messages) and has_quest_slots(schema)):
                return orig(*args, **kwargs)

            state["splits"] += 1
            split_id = state["splits"]
            if split_id <= LOG_LIMIT:
                write("split #{} begin".format(split_id))

            parts = {}
            for key in QUEST_SLOT_KEYS:
                single_messages = build_single_messages(messages, key)
                last_error = None
                got = None
                for attempt in range(1, max(1, int(MAX_ATTEMPTS)) + 1):
                    _slot.key = key
                    _slot.messages = single_messages
                    try:
                        # args の message を差し替えて呼ぶ
                        if len(args) >= 2:
                            call_args = args[:1] + (single_messages,) + args[2:]
                            call_kwargs = kwargs
                        else:
                            call_args = args
                            call_kwargs = dict(kwargs, message=single_messages)
                        result = orig(*call_args, **call_kwargs)
                        value = get_field(result, key)
                        if value is None and isinstance(result, dict) and len(result) == 1:
                            value = next(iter(result.values()))
                        if value is not None:
                            got = value
                            break
                        last_error = "missing {}".format(key)
                    except Exception as exc:
                        last_error = repr(exc)
                        if attempt >= MAX_ATTEMPTS:
                            _slot.key = None
                            _slot.messages = None
                            raise
                    finally:
                        _slot.key = None
                        _slot.messages = None
                    if attempt < MAX_ATTEMPTS and split_id <= LOG_LIMIT:
                        write("split #{} {} attempt {} failed: {}".format(
                            split_id, key, attempt, last_error))
                if got is None:
                    raise RuntimeError(
                        "quest split: {} failed after {} attempt(s): {}".format(
                            key, MAX_ATTEMPTS, last_error))
                parts[key] = got
                state["slots"] += 1
                if split_id <= LOG_LIMIT:
                    write("split #{} {} ok".format(split_id, key))

            merged = merge_slots(structure, parts)
            if split_id <= LOG_LIMIT:
                write("split #{} merged".format(split_id))
            return merged
        return send_request

    watch_aliases(ctx, [SEND_TARGET], install_send,
                  label="quest split generation")
    ctx.log("quest split generation: installed (attempts={})".format(
        MAX_ATTEMPTS))
