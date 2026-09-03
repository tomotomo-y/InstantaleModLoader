# -*- coding: utf-8 -*-
"""1131_ollama_disable_thinking をゲーム抜きで通す。

    python tools/tests/test_ollama_disable_thinking.py

偽の `SyncAPIClient.post` を差し込み、次を確認する。

  注入     … chat/completions の body に reasoning_effort=none が付く
  非破壊   … 呼び出し側の dict は書き換えない
  素通し   … responses や努力度付きの要求は触らない
  無効     … ENABLED=False ではフックを仕掛けない
"""
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
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


MOD_DIR, MOD = find_mod("_ollama_disable_thinking")
MOD_NAME = "ollama_disable_thinking_mod"
POST_TARGET = "openai._base_client:SyncAPIClient.post"

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class FakeCtx(object):
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.logs = []

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(MOD_NAME, None)
        raise
    return module


def fresh_mod(**settings):
    sys.modules.pop(MOD_NAME, None)
    module = load_mod()
    for key, value in settings.items():
        if not hasattr(module, key):
            raise SystemExit("設定 {!r} がモジュールに無い".format(key))
        setattr(module, key, value)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx


def call_post(ctx, path, body):
    """フック越しに post する。戻り値は orig が受け取った (path, body)。"""
    seen = {}

    def orig(self, path=None, *args, **kwargs):
        seen["path"] = path
        seen["body"] = kwargs.get("body")
        seen["body_id"] = id(kwargs.get("body"))
        return "ok"

    hook = ctx.hooks[POST_TARGET]
    result = hook(orig, object(), path, body=body)
    seen["result"] = result
    return seen


print("注入")
module, ctx = fresh_mod()
check("フックが掛かる", POST_TARGET in ctx.hooks, list(ctx.hooks))
body = {"model": "nemotron", "messages": [{"role": "user", "content": "hi"}]}
original_id = id(body)
seen = call_post(ctx, "/v1/chat/completions", body)
check("reasoning_effort=none が付く",
      seen["body"].get("reasoning_effort") == "none", seen["body"])
check("呼び出し側の dict は変わらない",
      "reasoning_effort" not in body and id(body) == original_id, body)
check("orig には別オブジェクトが渡る",
      seen["body_id"] != original_id, seen["body_id"])
check("結果は通る", seen["result"] == "ok")

print("素通し")
_, ctx2 = fresh_mod()
seen = call_post(ctx2, "/responses", {"model": "gpt-5.5", "input": []})
check("responses は触らない", "reasoning_effort" not in (seen["body"] or {}),
      seen["body"])

body_set = {"model": "x", "messages": [], "reasoning_effort": "low"}
seen = call_post(ctx2, "/chat/completions", body_set)
check("既存の reasoning_effort は上書きしない",
      seen["body"].get("reasoning_effort") == "low", seen["body"])

body_nested = {"model": "x", "messages": [],
               "reasoning": {"effort": "high"}}
seen = call_post(ctx2, "/v1/chat/completions?foo=1", body_nested)
check("既存の reasoning.effort も尊重する",
      seen["body"] is body_nested
      and "reasoning_effort" not in seen["body"], seen["body"])

print("無効")
_, ctx3 = fresh_mod(ENABLED=False)
check("ENABLED=False ではフック無し", POST_TARGET not in ctx3.hooks,
      list(ctx3.hooks))
check("off とログに残る",
      any("off" in msg for _, msg in ctx3.logs), ctx3.logs)

print("ヘルパ")
check("is_chat_completions がクエリ付きを拾う",
      module.is_chat_completions("/v1/chat/completions?x=1"))
check("is_chat_completions が別 path を弾く",
      not module.is_chat_completions("/v1/models"))

print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("全て通過")
