# -*- coding: utf-8 -*-
"""ゲームが LLM に載せる安全ポリシー指示を削り、違反判定の戻り値を無効化する。

openai_proxy の Instantale プロファイル（`messageTransforms` /
`responseOverrides`）と同じ A1–A8 を、プロセス内でやる。

## リクエスト側（A1–A4）

送信直前の本文から次を削る（プロキシと同じ順）:

  A2  TRPG GM の system で `【著作権・安全ポリシー違反の基準】` 以降を切断
  A1  同マーカーから次の `【` 見出しまでを削除
  A4  `- lawfulness_loss:` 行を削除
  A3  元本文にポリシー基準があるとき `- content_violation:` 行を削除

仕掛ける口は `instantale_modloader.llm.wrap_outgoing`（`111_` / `119_` と同じ）。

## レスポンス側（A5–A8）

`llm_manager:send_request` の戻り値を後処理する:

  A5  `content_violation` をネスト問わず false
  A6  `lawfulness_loss` をネスト問わず 0
  A7  `copyright_violation` / `policy_violation` を false
  A8  RPG 管理者評価のとき、存在するトップレベルだけ
      assessment / copyright_violation / policy_violation を問題なしへ

## 割り切り

  * `119_` の「他者犯罪のときだけ lawfulness_loss=0」より強い（常時 0）。
    同居するならこの MOD の応答強制が後勝ちになる想定
  * フレーズ接頭辞除去・思考 OFF・生成上限は対象外（別 MOD / 別目的）
"""

from instantale_modloader.llm import watch_aliases, wrap_outgoing

# --------------------------------------------------------------------------
# 設定（`mod.json` の "default" と揃えること）
# --------------------------------------------------------------------------
ENABLED = True
STRIP_PROMPTS = True          # A1–A4
FORCE_RESPONSE = True         # A5–A7
FORCE_ADMIN_PASS = True       # A8
LOG_LIMIT = 20

LOG_BASENAME = "policy_guardrail_bypass.log"

COPYRIGHT_POLICY_MARKER = "【著作権・安全ポリシー違反の基準】"
TRPG_GAME_MASTER_PREFIX = "あなたはTRPGのゲームマスターだ。"
RPG_ADMIN_VALIDATOR_PREFIX = "あなたはいわゆる大人向けのダークファンタジーRPGの管理者です"
ASSESSMENT_PASS = "著作権違反、違法・安全ポリシー違反、禁止表現には該当しません。"

SEND_TARGET = "scripts.llm.llm_manager:send_request"


# --------------------------------------------------------------------------
# 文字列演算（プロキシ lib/profile.js の applyOp と同じ）
# --------------------------------------------------------------------------
def truncate_from(content, marker):
    index = content.find(marker)
    if index == -1:
        return content
    return content[:index].rstrip()


def remove_section(content, marker, until_next):
    start = content.find(marker)
    if start == -1:
        return content
    nxt = content.find(until_next, start + len(marker))
    if nxt == -1:
        return content[:start]
    return content[:start] + content[nxt:]


def remove_line(content, marker):
    start = content.find(marker)
    if start == -1:
        return content
    end = content.find("\n", start)
    if end == -1:
        return content[:start]
    return content[:start] + content[end + 1:]


def transform_message(content):
    """1本文に A1–A4 をプロキシと同じ順で当てる。role は接頭辞で代用する。"""
    if not isinstance(content, str) or not content:
        return content
    original = content
    out = content
    # A2: TRPG GM はマーカー以降を末尾まで切る（startsWith で判定）
    if original.startswith(TRPG_GAME_MASTER_PREFIX):
        out = truncate_from(out, COPYRIGHT_POLICY_MARKER)
    # A1: セクション削除（次の 【 まで。無ければ末尾まで）
    out = remove_section(out, COPYRIGHT_POLICY_MARKER, "【")
    # A4
    out = remove_line(out, "- lawfulness_loss:")
    # A3: 判定はオリジナル（A1 でマーカーが消えても、元にあれば指示行を消す）
    if COPYRIGHT_POLICY_MARKER in original:
        out = remove_line(out, "- content_violation:")
    return out


# --------------------------------------------------------------------------
# レスポンス補正（プロキシ forceFieldDeep / setIfPresentTopLevel）
# --------------------------------------------------------------------------
def _is_plain(value):
    return value is None or isinstance(value, (str, bytes, int, float, bool))


def force_field_deep(value, field, forced):
    """`field` をネスト問わず `forced` にする。dict は新しい木、オブジェクトは破壊的。"""
    if _is_plain(value):
        return value
    if isinstance(value, list):
        return [force_field_deep(item, field, forced) for item in value]
    if isinstance(value, tuple):
        return tuple(force_field_deep(item, field, forced) for item in value)
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            out[key] = forced if key == field else force_field_deep(child, field, forced)
        return out
    # 属性付きオブジェクト（pydantic 等）
    try:
        names = list(vars(value))
    except TypeError:
        return value
    if field in names or hasattr(value, field):
        try:
            setattr(value, field, forced)
        except Exception:
            pass
    for name in names:
        if name == field or name.startswith("_"):
            continue
        try:
            child = getattr(value, name)
        except Exception:
            continue
        if _is_plain(child):
            continue
        try:
            setattr(value, name, force_field_deep(child, field, forced))
        except Exception:
            force_field_deep(child, field, forced)
    return value


def set_if_present_top_level(value, mapping):
    """トップレベルにキーがあるものだけ上書き。dict は写し、オブジェクトは破壊的。"""
    if isinstance(value, dict):
        out = dict(value)
        for key, forced in mapping.items():
            if key in out:
                out[key] = forced
        return out
    if _is_plain(value) or isinstance(value, (list, tuple)):
        return value
    for key, forced in mapping.items():
        if hasattr(value, key):
            try:
                setattr(value, key, forced)
            except Exception:
                pass
    return value


def has_system_prefix(messages, prefix):
    if not isinstance(messages, (list, tuple)):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.startswith(prefix):
            return True
    return False


_MISSING = object()


def _get_field(value, name):
    if isinstance(value, dict):
        return value.get(name, _MISSING)
    if _is_plain(value) or isinstance(value, (list, tuple)):
        return _MISSING
    return getattr(value, name, _MISSING)


def apply_response_overrides(value, messages, *, force_response=True,
                             force_admin_pass=True):
    """A5–A8。戻り値は `(結果, 何か書いたか)`。"""
    out = value
    changed = False
    if force_response:
        for field, forced in (
            ("content_violation", False),
            ("lawfulness_loss", 0),
            ("copyright_violation", False),
            ("policy_violation", False),
        ):
            before = _collect_field_values(out, field)
            out = force_field_deep(out, field, forced)
            after = _collect_field_values(out, field)
            if before != after:
                changed = True
    if force_admin_pass and has_system_prefix(messages, RPG_ADMIN_VALIDATOR_PREFIX):
        mapping = {
            "assessment": ASSESSMENT_PASS,
            "copyright_violation": False,
            "policy_violation": False,
        }
        before = {key: _get_field(out, key) for key in mapping}
        out = set_if_present_top_level(out, mapping)
        after = {key: _get_field(out, key) for key in mapping}
        if before != after:
            changed = True
    return out, changed


def _collect_field_values(value, field, sink=None):
    """ネスト内の `field` の値を出現順に集める（変化検知用）。"""
    if sink is None:
        sink = []
    if _is_plain(value):
        return sink
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_field_values(item, field, sink)
        return sink
    if isinstance(value, dict):
        if field in value:
            sink.append(value[field])
        for key, child in value.items():
            if key != field:
                _collect_field_values(child, field, sink)
        return sink
    try:
        names = list(vars(value))
    except TypeError:
        return sink
    if field in names:
        sink.append(getattr(value, field, None))
    for name in names:
        if name == field or name.startswith("_"):
            continue
        try:
            _collect_field_values(getattr(value, name), field, sink)
        except Exception:
            pass
    return sink


def message_arg(args, kwargs):
    """send_request の message 引数を取る。"""
    if len(args) >= 2 and isinstance(args[1], list):
        return args[1]
    message = kwargs.get("message")
    return message if isinstance(message, list) else None


def apply(ctx):
    if not ENABLED:
        ctx.log("policy guardrail bypass: off")
        return

    state = {"prompt": 0, "response": 0}
    write = ctx.logger(LOG_BASENAME)

    def rewrite(texts, site):
        if not STRIP_PROMPTS:
            return None
        if not isinstance(texts, (list, tuple)):
            return None
        changed = False
        out = []
        for text in texts:
            if not isinstance(text, str):
                out.append(text)
                continue
            new_text = transform_message(text)
            if new_text != text:
                changed = True
            out.append(new_text)
        if not changed:
            return None
        state["prompt"] += 1
        if state["prompt"] <= LOG_LIMIT:
            write("prompt stripped at {} (count={})".format(site, state["prompt"]))
        return out

    wrap_outgoing(ctx, rewrite, label="policy guardrail bypass")

    def install_send(target):
        @ctx.wrap(target, required=False, safe=True)
        def send_request(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            if not (FORCE_RESPONSE or FORCE_ADMIN_PASS):
                return result
            messages = message_arg(args, kwargs)
            try:
                new_result, changed = apply_response_overrides(
                    result, messages,
                    force_response=FORCE_RESPONSE,
                    force_admin_pass=FORCE_ADMIN_PASS)
            except Exception:
                ctx.log_exc("policy guardrail bypass: response override failed")
                return result
            if changed:
                state["response"] += 1
                if state["response"] <= LOG_LIMIT:
                    write("response forced (count={})".format(state["response"]))
            return new_result
        return send_request

    # 別名はプロバイダ初期化まで無いことがある
    watch_aliases(ctx, [SEND_TARGET], install_send,
                  label="policy guardrail bypass")

    parts = []
    if STRIP_PROMPTS:
        parts.append("strip")
    if FORCE_RESPONSE:
        parts.append("force")
    if FORCE_ADMIN_PASS:
        parts.append("admin")
    ctx.log("policy guardrail bypass: installed ({})".format(
        "+".join(parts) if parts else "noop"))
