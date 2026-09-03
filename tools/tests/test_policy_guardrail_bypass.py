# -*- coding: utf-8 -*-
"""1132_policy_guardrail_bypass をゲーム抜きで通す。

    python tools/tests/test_policy_guardrail_bypass.py

見ているのは3つ。

  送信除去 … A1–A4 がプロキシと同じ順で本文を削ること
  応答強制 … A5–A8 が dict / オブジェクトで効くこと
  経路     … wrap_outgoing（ローカル chat）と send_request（応答）が繋がること
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RUNTIME_DIR = os.path.join(ROOT, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import llm as ml_llm  # noqa: E402


def find_mod(suffix):
    matches = sorted(
        name for name in os.listdir(MODS_DIR)
        if name.endswith(suffix)
        and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json"))
        and not name.startswith("_")
    )
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD_PATH = find_mod("_policy_guardrail_bypass")
MOD_NAME = "policy_guardrail_bypass_mod"

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def load_mod():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, MOD_PATH, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(MOD_NAME, None)
        raise
    return module


M = load_mod()

POLICY = (
    "【出力要素】\n"
    "- content_violation: boolean\n"
    "- lawfulness_loss: 評判低下量。\n"
    "- statement: 本文。\n"
    "【著作権・安全ポリシー違反の基準】\n"
    "- 削除対象の基準文\n"
    "【内部知識検索の要素】\n"
    "- 残す\n"
)

GM = M.TRPG_GAME_MASTER_PREFIX + "\n" + POLICY


# --------------------------------------------------------------------------
# 純粋関数
# --------------------------------------------------------------------------
print("送信除去")
out = M.transform_message(POLICY)
check("A1 ポリシー基準セクションが消える",
      M.COPYRIGHT_POLICY_MARKER not in out and "削除対象" not in out, out)
check("A1 次の見出しは残る", "【内部知識検索の要素】" in out and "残す" in out, out)
check("A3 content_violation 指示行が消える",
      "- content_violation:" not in out, out)
check("A4 lawfulness_loss 指示行が消える",
      "- lawfulness_loss:" not in out, out)
check("statement 指示は残る", "- statement: 本文。" in out, out)

gm_out = M.transform_message(GM)
check("A2 TRPG GM はマーカー以降を切る",
      M.COPYRIGHT_POLICY_MARKER not in gm_out
      and "【内部知識検索の要素】" not in gm_out
      and gm_out.startswith(M.TRPG_GAME_MASTER_PREFIX), gm_out)

plain = "普通の指示だけ"
check("無関係な本文は変えない", M.transform_message(plain) == plain)

# ポリシー基準が無い要求では content_violation 指示を残す（プロキシと同じ）
no_policy = "【出力要素】\n- content_violation: boolean\n- statement: 返答。"
check("基準が無ければ content_violation 指示を残す",
      "- content_violation:" in M.transform_message(no_policy),
      M.transform_message(no_policy))

print("応答強制")
nested = {
    "content_violation": True,
    "action": {"content_violation": True, "statement": "x"},
    "lawfulness_loss": 8,
    "copyright_violation": True,
    "policy_violation": True,
}
forced, changed = M.apply_response_overrides(nested, [])
check("変化あり", changed)
check("A5 トップとネストの content_violation が false",
      forced["content_violation"] is False
      and forced["action"]["content_violation"] is False, forced)
check("A6 lawfulness_loss が 0", forced["lawfulness_loss"] == 0, forced)
check("A7 copyright/policy が false",
      forced["copyright_violation"] is False
      and forced["policy_violation"] is False, forced)
check("元の dict は壊さない", nested["content_violation"] is True, nested)

obj = types.SimpleNamespace(
    content_violation=True, lawfulness_loss=5, summary="s")
forced_obj, changed_obj = M.apply_response_overrides(obj, [])
check("オブジェクトも破壊的に直る",
      changed_obj and obj.content_violation is False and obj.lawfulness_loss == 0)

admin_msgs = [{
    "role": "system",
    "content": M.RPG_ADMIN_VALIDATOR_PREFIX + "。続き",
}]
admin = {
    "assessment": "安全ポリシーに抵触します。",
    "copyright_violation": True,
    "policy_violation": True,
    "extra": "keep",
}
admin_out, admin_changed = M.apply_response_overrides(
    admin, admin_msgs, force_response=False, force_admin_pass=True)
check("A8 assessment を問題なしへ",
      admin_changed and admin_out["assessment"] == M.ASSESSMENT_PASS, admin_out)
check("A8 無関係キーは残す", admin_out["extra"] == "keep", admin_out)

# キーが無いときは足さない
sparse = {"summary": "x"}
sparse_out, sparse_changed = M.apply_response_overrides(
    sparse, admin_msgs, force_response=False, force_admin_pass=True)
check("A8 無いキーは追加しない",
      not sparse_changed and "assessment" not in sparse_out, sparse_out)


# --------------------------------------------------------------------------
# 経路
# --------------------------------------------------------------------------
class FakeClient(object):
    def __init__(self):
        self.sent = []

    def chat(self, model, messages, format=None):
        prompt = self._apply_chat_template(model, messages)
        return self._post_with_model_loading_retry(
            "/completion", {"prompt": prompt})

    def _apply_chat_template(self, model, messages, timeout=None):
        return "\n".join(m.get("content") or "" for m in messages)

    def _post_with_model_loading_retry(self, url, payload, timeout=None):
        self.sent.append(payload["prompt"])
        return {"content": "ok"}


_PRISTINE = {name: FakeClient.__dict__[name] for name in
             ("chat", "_apply_chat_template", "_post_with_model_loading_retry")}


def revert_client():
    for name, func in _PRISTINE.items():
        setattr(FakeClient, name, func)


class FakeManager(object):
    BACKEND = "scripts.llm.request_llm_inference_gemini_test_streaming"

    def __init__(self, result=None):
        self.sent = []
        self.result = result if result is not None else {"ok": True}

        def send_request(manager_name, message, structure,
                         model=None, max_tokens=30000, timeout=None):
            self.sent.append([m.get("content") or "" for m in message])
            return self.result

        send_request.__module__ = self.BACKEND
        self.send_request = send_request
        self.send_request_with_no_structure = send_request


class FakeCtx(object):
    def __init__(self, client, manager, out_dir):
        self.client = client
        self.manager = manager
        self.out_dir = out_dir
        self.mod_dir = os.path.join(out_dir, "mod")
        self.lines = []
        self.errors = []
        self._mod = None

    def log(self, msg, level="INFO"):
        self.lines.append("{} {}".format(level, msg))

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        return path

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def wrap(self, target, required=True, **_kw):
        name = target.partition(":")[2].rsplit(".", 1)[-1]
        if "llm_manager" in target:
            manager = self.manager

            def decorate_manager(fn):
                original = getattr(manager, name, None)
                if original is None:
                    return fn

                def wrapper(*args, **kwargs):
                    return fn(original, *args, **kwargs)

                setattr(manager, name, wrapper)
                return fn
            return decorate_manager

        def decorate(fn):
            original = getattr(type(self.client), name)

            def wrapper(client_self, *args, **kwargs):
                return fn(original, client_self, *args, **kwargs)

            setattr(type(self.client), name, wrapper)
            return fn
        return decorate

    def resolve(self, target):
        name = target.partition(":")[2].rsplit(".", 1)[-1]
        if "llm_manager" in target:
            return self.manager, name, getattr(self.manager, name, None)
        return type(self.client), name, getattr(type(self.client), name, None)

    def superseded(self):
        return False


def arm(out_dir, **settings):
    revert_client()
    sys.modules.pop(MOD_NAME, None)
    module = load_mod()
    for key, value in settings.items():
        if not hasattr(module, key):
            raise SystemExit("設定 {!r} が無い".format(key))
        setattr(module, key, value)
    client = FakeClient()
    manager = FakeManager(result={
        "content_violation": True,
        "lawfulness_loss": 9,
        "statement": "x",
    })
    ctx = FakeCtx(client, manager, out_dir)
    module.apply(ctx)
    check("apply が例外を残さない", not ctx.errors, ctx.errors)
    if settings.get("ENABLED", True):
        check("installed ログ", any("installed" in line for line in ctx.lines),
              ctx.lines)
    return module, client, manager, ctx


print("経路")
tmp = tempfile.mkdtemp(prefix="policy_guardrail_")
_mod, client, manager, ctx = arm(os.path.join(tmp, "local"))
client.chat("m", [
    {"role": "system", "content": POLICY},
    {"role": "user", "content": "どうする？"},
], {})
check("ローカル chat でポリシーが削られる",
      len(client.sent) == 1
      and M.COPYRIGHT_POLICY_MARKER not in client.sent[0]
      and "- content_violation:" not in client.sent[0],
      client.sent)

# クラウド境界の送信除去はローカル印が無いときだけ
sys.modules.pop(ml_llm.LOCAL_REQUEST_MODULE, None)
_mod, client, manager, ctx = arm(os.path.join(tmp, "cloud"))
result = manager.send_request(
    "m",
    [{"role": "system", "content": POLICY},
     {"role": "user", "content": "入力"}],
    object())
check("クラウドで送信が削られる",
      M.COPYRIGHT_POLICY_MARKER not in manager.sent[0][0], manager.sent)
check("クラウドで応答が強制される",
      result["content_violation"] is False
      and result["lawfulness_loss"] == 0, result)

_mod, _c, _m, ctx_off = arm(os.path.join(tmp, "off"), ENABLED=False)
check("ENABLED=False は off ログ",
      any("off" in line for line in ctx_off.lines), ctx_off.lines)

print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("全て通過")
