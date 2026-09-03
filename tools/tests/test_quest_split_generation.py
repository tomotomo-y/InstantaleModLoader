# -*- coding: utf-8 -*-
"""1134_quest_split_generation をゲーム抜きで通す。"""
import importlib.util
import io
import json
import os
import sys
import tempfile

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
        raise SystemExit("cannot find *{}".format(suffix))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD_PATH = find_mod("_quest_split_generation")
MOD_NAME = "quest_split_generation_mod"
failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def load_mod():
    sys.modules.pop(MOD_NAME, None)
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, MOD_PATH, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    spec.loader.exec_module(module)
    return module


M = load_mod()

SCHEMA = {
    "type": "object",
    "properties": {
        "quest_1": {"type": "object"},
        "quest_2": {"type": "object"},
        "quest_3": {"type": "object"},
    },
    "required": ["quest_1", "quest_2", "quest_3"],
    "$defs": {
        "QuestStructure": {
            "properties": {
                "enemies": {"type": "array", "items": {"type": "object"}},
            }
        }
    },
}

MESSAGES = [
    {
        "role": "system",
        "content": (
            "あなたはRPGのクエストジェネレータです。3つの【討伐】クエストを"
            "生成してください。\n"
            + M.CLIENT_ENEMY_COUNT_INSTRUCTIONS[0]
        ),
    },
    {
        "role": "user",
        "content": "【要件】\n- quest_1:難易度10\n- quest_2:難易度20\n- quest_3:難易度30",
    },
]

print("整形")
check("スロット検知", M.has_quest_slots(SCHEMA))
single = M.single_slot_schema(SCHEMA, "quest_2")
check("1スロット schema",
      list(single["properties"].keys()) == ["quest_2"]
      and single["required"] == ["quest_2"])
check("enemies maxItems",
      single["$defs"]["QuestStructure"]["properties"]["enemies"]["maxItems"]
      == M.QUEST_ENEMIES_MAX_ITEMS)

built = M.build_single_messages(MESSAGES, "quest_2")
sys_text = built[0]["content"]
user_text = built[1]["content"]
check("3つ→1つ", "1つの【討伐】クエスト" in sys_text and "3つの【討伐】" not in sys_text)
check("敵指示上書き", M.REWRITTEN_ENEMY_COUNT_INSTRUCTION in sys_text)
check("quest_2 だけ残る",
      "- quest_2:" in user_text
      and "- quest_1:" not in user_text
      and "- quest_3:" not in user_text, user_text)
check("今回の生成対象", "quest_2 だけを生成" in user_text)

print("結合")
merged = M.merge_slots(dict, {
    "quest_1": {"t": 1},
    "quest_2": {"t": 2},
    "quest_3": {"t": 3},
})
check("dict 結合", merged == {"quest_1": {"t": 1}, "quest_2": {"t": 2}, "quest_3": {"t": 3}})

print("経路")


class FakeManager(object):
    BACKEND = "scripts.llm.request_llm_inference_gemini_test_streaming"

    def __init__(self):
        self.calls = []

        def send_request(manager_name, message, structure,
                         model=None, max_tokens=30000, timeout=None):
            self.calls.append([m.get("content") or "" for m in message])
            # スロット名を本文から推定
            blob = "\n".join(self.calls[-1])
            for key in M.QUEST_SLOT_KEYS:
                if "{} だけを生成".format(key) in blob:
                    return {key: {"title": key}}
            return {"quest_1": {"title": "all"}}

        send_request.__module__ = self.BACKEND
        self.send_request = send_request


class FakeCtx(object):
    def __init__(self, manager, out_dir):
        self.manager = manager
        self.client = object()
        self.out_dir = out_dir
        self.mod_dir = os.path.join(out_dir, "mod")
        self.lines = []
        self.errors = []
        self._mod = None

    def log(self, msg, level="INFO"):
        self.lines.append("{} {}".format(level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

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

            def decorate(fn):
                original = getattr(manager, name, None)
                if original is None:
                    return fn

                def wrapper(*args, **kwargs):
                    return fn(original, *args, **kwargs)

                setattr(manager, name, wrapper)
                return fn
            return decorate

        def decorate_noop(fn):
            return fn
        return decorate_noop

    def resolve(self, target):
        name = target.partition(":")[2].rsplit(".", 1)[-1]
        if "llm_manager" in target:
            return self.manager, name, getattr(self.manager, name, None)
        return None, name, None

    def superseded(self):
        return False


tmp = tempfile.mkdtemp(prefix="quest_split_")
sys.modules.pop(ml_llm.LOCAL_REQUEST_MODULE, None)
mod = load_mod()
manager = FakeManager()
ctx = FakeCtx(manager, tmp)
mod.apply(ctx)
check("installed", any("installed" in line for line in ctx.lines), ctx.lines)
check("apply 例外なし", not ctx.errors, ctx.errors)

result = manager.send_request("m", MESSAGES, SCHEMA)
check("3回呼ばれる", len(manager.calls) == 3, len(manager.calls))
check("結合結果",
      result.get("quest_1", {}).get("title") == "quest_1"
      and result.get("quest_2", {}).get("title") == "quest_2"
      and result.get("quest_3", {}).get("title") == "quest_3",
      result)

# 非対象はそのまま1回
manager.calls.clear()
plain = [{"role": "user", "content": "hi"}]
out = manager.send_request("m", plain, {"properties": {"x": {}}})
check("非対象は1回", len(manager.calls) == 1, manager.calls)

print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("全て通過")
