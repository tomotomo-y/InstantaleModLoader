# -*- coding: utf-8 -*-
"""クエスト敵の水増しを抑え、大規模生成の出力枠を広げる。

openai_proxy Instantale プロファイルの次をプロセス内でやる。

  * クエストジェネレータ向け: 敵数指示の上書き・終了規則の追記
    （structure が dict schema のとき enemies.maxItems=6）
  * ワールドマップ／クエストジェネレータ／TRPG要約: 出力トークン上限を
    既定 32768 まで引き上げ（プロキシの areaDetailNumPredict 相当）

クエスト3件の分割生成は別 MOD（`1134_quest_split_generation`）。
"""

from instantale_modloader.llm import watch_aliases, wrap_outgoing

# --------------------------------------------------------------------------
# 設定（`mod.json` の "default" と揃えること）
# --------------------------------------------------------------------------
ENABLED = True
ENEMY_CAPS = True
WIDEN_OUTPUT = True
MAX_OUTPUT_TOKENS = 32768
QUEST_ENEMIES_MAX_ITEMS = 6
LOG_LIMIT = 20

LOG_BASENAME = "generation_limits.log"

QUEST_GENERATOR_MARK = "クエストジェネレータです"
QUEST_GENERATOR_SYSTEM_PREFIX = "あなたはRPGのクエストジェネレータです"
TRPG_SUMMARY_SYSTEM_PREFIX = "あなたはTRPGの出来事を要約する担当だ"
WORLD_MAP_SYSTEM_PREFIX = "あなたはダークファンタジーRPGのワールドマップジェネレーターです"

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

QUEST_ENEMY_OUTPUT_LIMITS = (
    "【出力上限・終了規則】\n"
    "- enemies 配列は合計2〜6件。typeごと上限: normal 最大3種、miniboss 最大3種。"
    "boss は enemies に入れない。\n"
    "- 上限に達したら直ちに配列を閉じる。同じ敵・別名・強化版・サイズ違いでの"
    "水増しは禁止。\n"
    "- クエストを1件完成したら、直ちにJSONを閉じる。説明文、追加クエスト、"
    "続きの出力は禁止する。"
)

SEND_TARGET = "scripts.llm.llm_manager:send_request"
POST_TARGET = (
    "llama_cpp_runtime_completion:LlamaCppClient._post_with_model_loading_retry"
)


def is_quest_generator_text(content):
    return isinstance(content, str) and QUEST_GENERATOR_MARK in content


def rewrite_enemy_count_instructions(content):
    out = content
    for instruction in CLIENT_ENEMY_COUNT_INSTRUCTIONS:
        if instruction in out:
            out = out.replace(instruction, REWRITTEN_ENEMY_COUNT_INSTRUCTION)
    return out


def transform_quest_generator_message(content):
    """クエストジェネレータの system 本文へ敵抑制を載せる。"""
    if not is_quest_generator_text(content):
        return content
    out = rewrite_enemy_count_instructions(content)
    if "【出力上限・終了規則】" not in out:
        out = out.rstrip() + "\n" + QUEST_ENEMY_OUTPUT_LIMITS
    return out


def is_max_context_messages(messages):
    if not isinstance(messages, (list, tuple)):
        return False
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if content.startswith(WORLD_MAP_SYSTEM_PREFIX) or (
                content.startswith("あなたは")
                and "ワールドマップジェネレーター" in content):
            return True
        if (content.startswith(QUEST_GENERATOR_SYSTEM_PREFIX)
                or QUEST_GENERATOR_MARK in content):
            return True
        if content.startswith(TRPG_SUMMARY_SYSTEM_PREFIX):
            return True
    return False


def is_max_context_blob(text):
    if not isinstance(text, str):
        return False
    return (
        WORLD_MAP_SYSTEM_PREFIX in text
        or "ワールドマップジェネレーター" in text
        or QUEST_GENERATOR_MARK in text
        or TRPG_SUMMARY_SYSTEM_PREFIX in text
    )


def with_enemies_max_items(schema, max_items=None):
    """dict スキーマに enemies.maxItems を付ける。写しを返す。触れなければ None。"""
    max_items = QUEST_ENEMIES_MAX_ITEMS if max_items is None else max_items
    if not isinstance(schema, dict):
        return None
    out = None

    defs = schema.get("$defs") or schema.get("definitions")
    if isinstance(defs, dict) and "QuestStructure" in defs:
        quest = defs.get("QuestStructure")
        props = quest.get("properties") if isinstance(quest, dict) else None
        enemies = props.get("enemies") if isinstance(props, dict) else None
        if isinstance(enemies, dict) and enemies.get("type") == "array":
            out = dict(schema)
            new_defs = dict(defs)
            new_quest = dict(quest)
            new_props = dict(props)
            new_enemies = dict(enemies)
            new_enemies["maxItems"] = max_items
            new_props["enemies"] = new_enemies
            new_quest["properties"] = new_props
            new_defs["QuestStructure"] = new_quest
            if "$defs" in schema:
                out["$defs"] = new_defs
            else:
                out["definitions"] = new_defs

    props = schema.get("properties")
    if isinstance(props, dict):
        enemies = props.get("enemies")
        if (isinstance(enemies, dict) and enemies.get("type") == "array"
                and props.get("quest_title") and props.get("boss")):
            base = out if out is not None else dict(schema)
            new_props = dict(base.get("properties") or props)
            new_enemies = dict(enemies)
            new_enemies["maxItems"] = max_items
            new_props["enemies"] = new_enemies
            base["properties"] = new_props
            out = base
    return out


def bump_max_tokens_args(args, kwargs, limit):
    """send_request の max_tokens を limit 以上にする。"""
    if "max_tokens" in kwargs:
        current = kwargs.get("max_tokens")
        if isinstance(current, int) and current >= limit:
            return args, kwargs, False
        return args, dict(kwargs, max_tokens=limit), True
    if len(args) >= 5 and isinstance(args[4], int):
        if args[4] >= limit:
            return args, kwargs, False
        return args[:4] + (limit,) + args[5:], kwargs, True
    return args, dict(kwargs, max_tokens=limit), True


def message_arg(args, kwargs):
    if len(args) >= 2 and isinstance(args[1], list):
        return args[1]
    message = kwargs.get("message")
    return message if isinstance(message, list) else None


def structure_arg(args, kwargs):
    if len(args) >= 3:
        return args[2]
    return kwargs.get("structure")


def apply(ctx):
    if not ENABLED:
        ctx.log("generation limits: off")
        return

    state = {"prompt": 0, "tokens": 0, "schema": 0}
    write = ctx.logger(LOG_BASENAME)

    def rewrite(texts, site):
        if not ENEMY_CAPS:
            return None
        if not isinstance(texts, (list, tuple)):
            return None
        changed = False
        out = []
        for text in texts:
            if not isinstance(text, str):
                out.append(text)
                continue
            new_text = transform_quest_generator_message(text)
            if new_text != text:
                changed = True
            out.append(new_text)
        if not changed:
            return None
        state["prompt"] += 1
        if state["prompt"] <= LOG_LIMIT:
            write("enemy caps at {} (count={})".format(site, state["prompt"]))
        return out

    wrap_outgoing(ctx, rewrite, label="generation limits")

    def install_send(target):
        @ctx.wrap(target, required=False, safe=True)
        def send_request(orig, *args, **kwargs):
            messages = message_arg(args, kwargs)
            if WIDEN_OUTPUT and is_max_context_messages(messages):
                args, kwargs, bumped = bump_max_tokens_args(
                    args, kwargs, MAX_OUTPUT_TOKENS)
                if bumped:
                    state["tokens"] += 1
                    if state["tokens"] <= LOG_LIMIT:
                        write("widen max_tokens={} (count={})".format(
                            MAX_OUTPUT_TOKENS, state["tokens"]))

            if ENEMY_CAPS:
                structure = structure_arg(args, kwargs)
                if isinstance(structure, dict):
                    patched = with_enemies_max_items(structure)
                    if patched is not None and patched != structure:
                        if len(args) >= 3:
                            args = args[:2] + (patched,) + args[3:]
                        else:
                            kwargs = dict(kwargs, structure=patched)
                        state["schema"] += 1
                        if state["schema"] <= LOG_LIMIT:
                            write("enemies maxItems={} (count={})".format(
                                QUEST_ENEMIES_MAX_ITEMS, state["schema"]))
            return orig(*args, **kwargs)
        return send_request

    watch_aliases(ctx, [SEND_TARGET], install_send, label="generation limits")

    if WIDEN_OUTPUT:
        @ctx.wrap(POST_TARGET, required=False, safe=True)
        def post_with_retry(orig, self, url, payload, timeout=None, *a, **kw):
            if isinstance(payload, dict):
                prompt = payload.get("prompt")
                if is_max_context_blob(prompt):
                    new_payload = None
                    for key in ("n_predict", "max_tokens", "max_completion_tokens"):
                        current = payload.get(key)
                        if isinstance(current, int) and current < MAX_OUTPUT_TOKENS:
                            if new_payload is None:
                                new_payload = dict(payload)
                            new_payload[key] = MAX_OUTPUT_TOKENS
                    if new_payload is None and "n_predict" not in payload:
                        new_payload = dict(payload)
                        new_payload["n_predict"] = MAX_OUTPUT_TOKENS
                    if new_payload is not None:
                        payload = new_payload
                        state["tokens"] += 1
                        if state["tokens"] <= LOG_LIMIT:
                            write("widen local payload (count={})".format(
                                state["tokens"]))
            return orig(self, url, payload, timeout, *a, **kw)

    parts = []
    if ENEMY_CAPS:
        parts.append("enemy")
    if WIDEN_OUTPUT:
        parts.append("tokens={}".format(MAX_OUTPUT_TOKENS))
    ctx.log("generation limits: installed ({})".format(
        "+".join(parts) if parts else "noop"))
