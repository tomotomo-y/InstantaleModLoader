# -*- coding: utf-8 -*-
"""211_probe_text_viewport をゲーム抜きで通す。

    python tools/test_text_viewport_probe.py

読み取り専用であること、対象4メソッドを包むこと、本文ラベルから親の表示領域まで
同じ実寸を記録することを確認する。
"""

import importlib.util
import io
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def mod_path():
    folder = os.path.join(MODS_DIR, "211_probe_text_viewport")
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        return os.path.join(folder, json.load(fh)["entry"])


MOD = mod_path()
FAILURES = []


def check(name, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


class Widget(object):
    def __init__(self, parent=None, size=(100, 100), pos=(0, 0), **attrs):
        self.parent = parent
        self.size = size
        self.pos = pos
        self.width, self.height = size
        self.x, self.y = pos
        self.size_hint = (None, None)
        self.pos_hint = {}
        self.children = []
        for name, value in attrs.items():
            setattr(self, name, value)


class FakeHUD(object):
    def __init__(self):
        self.viewport = Widget(size=(1000, 400), pos=(50, 100),
                               do_scroll_y=True, scroll_y=1.0)
        self.text_display = Widget(parent=self.viewport, size=(1000, 900),
                                   pos=(0, 0), text="")
        self.display_text = ""
        self.viewport.children = [self.text_display]
        self.text_input_layout = Widget(size=(1000, 90), pos=(50, 10))
        self.calls = []

    def update_text_display_size(self, *args):
        self.calls.append("size")

    def _on_scroll_resize(self, instance, value):
        self.calls.append("scroll")

    def _on_text_input_layout_resize(self, *args):
        self.calls.append("input")

    def update_display_text(self, instance, value):
        self.calls.append("display")
        self.display_text = value
        self.text_display.text = "表示: " + value


class FakeApp(object):
    """本文の経路を、実機で見えている順に真似る。

    `add_text` が待ち行列へ積み、`process_text_queue` が空いていれば取り出して
    `add_text_display` の鎖を始める。`add_text_immediately` は行列を通さない。
    """

    def __init__(self):
        self.display_text = ""
        self.is_adding_text = False
        self.text_queue = []

    def add_text(self, context):
        self.text_queue.append(context)

    def process_text_queue(self, _dt):
        if self.is_adding_text or not self.text_queue:
            return
        context = self.text_queue.pop(0)
        self.is_adding_text = True
        self.add_text_display(0, context, -1)

    def add_text_immediately(self, content):
        self.display_text += content

    def wait_for_add_text(self):
        return self.is_adding_text

    def add_text_display(self, _dt, context, index=-1):
        position = index + 1
        if position < len(context):
            self.display_text += context[position]
        if position >= len(context) - 1:
            self.is_adding_text = False


PRISTINE = {
    name: getattr(FakeHUD, name)
    for name in ("update_text_display_size", "_on_scroll_resize",
                 "_on_text_input_layout_resize", "update_display_text")
}
PRISTINE_APP = {
    name: getattr(FakeApp, name)
    for name in ("add_text", "add_text_display", "add_text_immediately",
                 "process_text_queue", "wait_for_add_text")
}


class FakeCtx(object):
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.wrapped = {}
        self.errors = []

    def wrap(self, target, **_kwargs):
        def decorate(func):
            _module, qualified = target.split(":")
            _cls, method = qualified.split(".")
            owner = FakeApp if _cls == "InstantaleApp" else FakeHUD
            original = getattr(owner, method)

            def wrapper(self, *args, **kwargs):
                return func(original, self, *args, **kwargs)

            setattr(owner, method, wrapper)
            self.wrapped[target] = wrapper
            return func
        return decorate

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def log(self, _message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "mod_text_viewport_probe", MOD,
        submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run():
    module = types.ModuleType("scripts.hud.new_hud")
    module.InstanTaleHUD = FakeHUD
    sys.modules[module.__name__] = module
    for name, method in PRISTINE.items():
        setattr(FakeHUD, name, method)
    for name, method in PRISTINE_APP.items():
        setattr(FakeApp, name, method)

    with tempfile.TemporaryDirectory() as out_dir:
        ctx = FakeCtx(out_dir)
        load_mod().apply(ctx)
        expected = {
            "scripts.hud.new_hud:InstanTaleHUD.update_text_display_size",
            "scripts.hud.new_hud:InstanTaleHUD._on_scroll_resize",
            "scripts.hud.new_hud:InstanTaleHUD._on_text_input_layout_resize",
            "scripts.hud.new_hud:InstanTaleHUD.update_display_text",
            "__main__:InstantaleApp.add_text_display",
            "__main__:InstantaleApp.add_text",
            "__main__:InstantaleApp.add_text_immediately",
            "__main__:InstantaleApp.process_text_queue",
            "__main__:InstantaleApp.wait_for_add_text",
        }
        check("all resize and text paths are wrapped",
              set(ctx.wrapped) == expected, ctx.wrapped)

        hud = FakeHUD()
        before = (hud.viewport.size, hud.viewport.pos,
                  hud.text_display.size, hud.text_display.pos)
        hud.update_text_display_size()
        hud._on_scroll_resize(hud.viewport, hud.viewport.size)
        hud._on_text_input_layout_resize()
        hud.update_display_text(hud, "本文")
        app = FakeApp()
        for index in (-1, 0, 1):
            app.add_text_display(0, "本文。", index)

        # 待ち行列を通る経路。積んで、取り出して、流し込みが終わるまで。
        queued = FakeApp()
        queued.add_text("一つ目")
        queued.add_text("二つ目")
        check("the probe leaves the queue alone",
              queued.text_queue == ["一つ目", "二つ目"], queued.text_queue)
        queued.process_text_queue(0)
        for index in range(0, 3):
            queued.add_text_display(0, "一つ目", index)
        check("the queue drains through the game's own path",
              queued.text_queue == ["二つ目"] and queued.display_text == "一つ目",
              (queued.text_queue, queued.display_text))
        queued.wait_for_add_text()
        queued.add_text_immediately("割り込み")
        check("immediate display still bypasses the queue",
              queued.text_queue == ["二つ目"]
              and queued.display_text == "一つ目割り込み",
              (queued.text_queue, queued.display_text))

        after = (hud.viewport.size, hud.viewport.pos,
                 hud.text_display.size, hud.text_display.pos)
        check("probe does not change geometry", after == before, (before, after))
        check("original methods still run",
              hud.calls == ["size", "scroll", "input", "display"], hud.calls)

        with io.open(os.path.join(out_dir, "text_viewport.log"), encoding="utf-8") as fh:
            log = fh.read()
        check("log includes the viewport", log.count("Widget") >= 2, log)
        check("log includes the text display parent chain",
              "parent[0]" in log and "parent[1]" in log, log)
        check("display updates log only text lengths and hashes",
              "text: argument=str(len=2," in log
              and "display_text=str(len=2," in log
              and "label=str(len=6," in log, log)
        check("log includes text streaming start and completion",
              "stream: context=str(len=3," in log
              and "is_adding_text True -> True" in log
              and "is_adding_text True -> False" in log, log)
        check("log follows the text through the queue",
              "add_text: context=str(len=3," in log
              and "text_queue[1]" in log and "text_queue[2]" in log, log)
        check("log records when the queue is drained",
              "process_text_queue: " in log and "-> " in log, log)
        check("log records the immediate path and the wait",
              "add_text_immediately: content=str(len=4," in log
              and "wait_for_add_text: " in log, log)
        check("the flow log only carries lengths and hashes, never the text",
              "一つ目" not in log and "割り込み" not in log, log)
        check("no probe error was swallowed", not ctx.errors, ctx.errors)

    print()
    if FAILURES:
        print("FAILED: {}".format(", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
