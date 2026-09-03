# -*- coding: utf-8 -*-
"""1133_generation_limits をゲーム抜きで通す。"""
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RUNTIME_DIR = os.path.join(ROOT, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


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


MOD_DIR, MOD_PATH = find_mod("_generation_limits")
MOD_NAME = "generation_limits_mod"
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

print("敵抑制")
src = (
    "あなたはRPGのクエストジェネレータです。3つの【討伐】クエストを生成してください。\n"
    + M.CLIENT_ENEMY_COUNT_INSTRUCTIONS[0]
)
out = M.transform_quest_generator_message(src)
check("敵数指示が上書きされる",
      M.CLIENT_ENEMY_COUNT_INSTRUCTIONS[0] not in out
      and M.REWRITTEN_ENEMY_COUNT_INSTRUCTION in out, out)
check("終了規則が付く", "【出力上限・終了規則】" in out, out)
check("二度目は増やさない",
      M.transform_quest_generator_message(out).count("【出力上限・終了規則】") == 1)
check("無関係は変えない", M.transform_quest_generator_message("hello") == "hello")

print("schema")
schema = {
    "$defs": {
        "QuestStructure": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "enemies": {"type": "array", "items": {"type": "object"}},
            },
        }
    },
    "properties": {
        "quest_1": {"$ref": "#/$defs/QuestStructure"},
        "quest_2": {"$ref": "#/$defs/QuestStructure"},
        "quest_3": {"$ref": "#/$defs/QuestStructure"},
    },
    "required": ["quest_1", "quest_2", "quest_3"],
}
patched = M.with_enemies_max_items(schema)
check("maxItems が付く",
      patched["$defs"]["QuestStructure"]["properties"]["enemies"]["maxItems"]
      == M.QUEST_ENEMIES_MAX_ITEMS)
check("元は壊さない", "maxItems" not in schema["$defs"]["QuestStructure"]["properties"]["enemies"])

print("大規模検知")
check("ワールドマップを検知",
      M.is_max_context_messages([{
          "role": "system",
          "content": M.WORLD_MAP_SYSTEM_PREFIX + "。",
      }]))
check("クエストジェネレータを検知",
      M.is_max_context_messages([{
          "role": "system",
          "content": "あなたはクエストジェネレータです。",
      }]))
check("無関係は検知しない",
      not M.is_max_context_messages([{"role": "user", "content": "hi"}]))

args, kwargs, bumped = M.bump_max_tokens_args((), {"max_tokens": 1000}, 32768)
check("max_tokens を引き上げる", bumped and kwargs["max_tokens"] == 32768)
args2, kwargs2, bumped2 = M.bump_max_tokens_args((), {"max_tokens": 40000}, 32768)
check("既に広いときは触らない", not bumped2 and kwargs2["max_tokens"] == 40000)

print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("全て通過")
