# -*- coding: utf-8 -*-
"""計測: メッセージ表示領域を伸ばすための実寸と更新順を記録する。

本文ラベル `hud.text_display` は内容の高さであり、画面に見えている領域そのものでは
ない。親を辿り、ScrollView または固定高レイアウトを実測してから変更対象を決める。

本文がラベルへ届くまでの経路も記録する。`116_` が `add_text_display` を横取りして
一括表示に差し替えたところ、**LLM の長文が1文字も出なくなった**（2026-08-01 の実機）。
そのとき長文は `add_text_display` に一度も届いておらず、落ちているのは描画層ではなく
その手前 ― `add_text` が積み、`process_text_queue` が取り出す待ち行列だと分かった。
ゲーム本体は読めないので、行列の出入りを実測する以外に確かめる手段が無い。

この MOD は値を一切変更しない。包んだ関数はすべて素の実装を呼び、その前後を
記録するだけである。
"""

import datetime
import hashlib
import sys
import time

from instantale_modloader import frames, ui

LOG_BASENAME = "text_viewport.log"
MAX_DEPTH = 6
MAX_SAMPLES = 30
TEXT_SAMPLE_CHARS = 100
MAX_STREAM_EVENTS = 30
MAX_FLOW_EVENTS = 300
MAX_BATCH_SAMPLES = 30

# 待ち行列を名前で決めつけない。名前にこの語を含む入れ物と真偽値を**全部**並べ、
# どれが動いているかは記録を見てから決める（`ui.py` の「フラグ名を信用しない」）。
QUEUE_NAME_HINTS = ("text", "queue")

ATTRS = ("size", "pos", "size_hint", "pos_hint", "width", "height",
         "x", "y", "padding", "spacing", "orientation", "opacity",
         "scroll_x", "scroll_y", "scroll_type", "do_scroll_x", "do_scroll_y")


def apply(ctx):
    new_hud = sys.modules.get("scripts.hud.new_hud")
    if new_hud is None or getattr(new_hud, "InstanTaleHUD", None) is None:
        ctx.log("message viewport probe: InstanTaleHUD not loaded; skipping",
                level="WARN")
        return

    log_path = ctx.out_path(LOG_BASENAME)
    seen = set()
    state = {
        "samples": 0,
        "text_length": None,
        "text_mismatch": None,
        "stream_calls": 0,
        "stream_events": 0,
        "flow_events": 0,
        "batch_samples": 0,
        "queue": None,
    }

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"),
                    text))
        except Exception:
            ctx.log_exc("message viewport probe: write failed")

    def values(widget):
        bits = ["{}#{:x}".format(type(widget).__name__, id(widget))]
        for name in ATTRS:
            value = frames.attr(widget, name)
            if value is not frames.MISSING:
                bits.append("{}={}".format(name, frames.repr_value(value)))
        return " ".join(bits)

    def text_display(hud):
        widget = frames.attr(hud, "text_display")
        return None if widget in (None, frames.MISSING) else widget

    def text_summary(value):
        if not isinstance(value, str):
            return "{}".format(type(value).__name__)
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        return "str(len={}, sha256={})".format(len(value), digest)

    def queue_snapshot(app):
        """本文の待ち行列になりうるものを、名前と長さで並べる。**読むだけ。**"""
        try:
            items = sorted(vars(app).items())
        except Exception:
            return "(no vars)"
        bits = []
        for name, value in items:
            lowered = name.lower()
            if not any(hint in lowered for hint in QUEUE_NAME_HINTS):
                continue
            if isinstance(value, bool):
                bits.append("{}={}".format(name, value))
                continue
            if value is None or isinstance(value, (str, bytes, dict)):
                continue
            try:
                bits.append("{}[{}]".format(name, len(value)))
            except TypeError:
                continue
        return " ".join(bits) or "(nothing queue-like)"

    def flow(event, detail):
        """本文の経路の1行。上限を置くのは、毎フレーム走る経路があるため。"""
        if state["flow_events"] >= MAX_FLOW_EVENTS:
            return
        state["flow_events"] += 1
        write("{}: {}".format(event, detail))

    def matching_suffix(left, right):
        count = 0
        for left_char, right_char in zip(reversed(left), reversed(right)):
            if left_char != right_char:
                break
            count += 1
        return count

    def batch_snapshot(app, context, delay):
        if state["batch_samples"] >= MAX_BATCH_SAMPLES:
            return
        try:
            hud = ui.find_hud(app)
            label = text_display(hud)
            scroll = frames.attr(hud, "scroll_view")
            shown = frames.attr(label, "text")
            if (label is None or scroll in (None, frames.MISSING)
                    or not isinstance(shown, str)):
                return
            state["batch_samples"] += 1
            texture_size = frames.attr(label, "texture_size", (None, None))
            write("batch_view delay={:.2f}s context={} suffix={} label={} "
                  "texture_height={} label_height={} scroll_height={} scroll_y={} "
                  "padding={}".format(
                      delay, text_summary(context),
                      matching_suffix(shown, context), text_summary(shown),
                      texture_size[1] if isinstance(texture_size, (tuple, list))
                      and len(texture_size) > 1 else "?",
                      frames.attr(label, "height"),
                      frames.attr(scroll, "height"),
                      frames.attr(scroll, "scroll_y"),
                      frames.repr_value(frames.attr(label, "padding"))))
        except Exception:
            ctx.log_exc("message viewport probe: cannot snapshot batch view")

    def schedule_batch_snapshots(app, context):
        try:
            from kivy.clock import Clock
        except Exception:
            batch_snapshot(app, context, 0)
            return
        for delay in (0, 0.5):
            try:
                Clock.schedule_once(
                    lambda _dt, delay=delay: batch_snapshot(app, context, delay),
                    delay)
            except Exception:
                ctx.log_exc("message viewport probe: cannot schedule batch view")

    def snapshot(hud, event, value=None):
        label = text_display(hud)
        if label is None or state["samples"] >= MAX_SAMPLES:
            return

        nodes = []
        node = label
        for _depth in range(MAX_DEPTH):
            if node in (None, frames.MISSING):
                break
            nodes.append(node)
            node = frames.attr(node, "parent")
        shown = frames.attr(label, "text")
        display = frames.attr(hud, "display_text")
        if event == "update_display_text":
            lengths = [len(text) for text in (value, display, shown)
                       if isinstance(text, str)]
            length = max(lengths) if lengths else 0
            mismatch = isinstance(value, str) and (
                not isinstance(shown, str) or value not in shown)
            previous = state["text_length"]
            previous_mismatch = state["text_mismatch"]
            state["text_length"] = length
            state["text_mismatch"] = mismatch
            if (previous is not None
                    and length // TEXT_SAMPLE_CHARS == previous // TEXT_SAMPLE_CHARS
                    and mismatch == previous_mismatch):
                return
        signature = (event, text_summary(value), text_summary(display),
                     text_summary(shown), tuple(
            (id(node), frames.attr(node, "width"), frames.attr(node, "height"),
             frames.repr_value(frames.attr(node, "pos")))
            for node in nodes))
        if signature in seen:
            return
        seen.add(signature)
        state["samples"] += 1
        write("=" * 72)
        write("{} sample={}".format(event, state["samples"]))
        if event == "update_display_text":
            write("  text: argument={} display_text={} label={}".format(
                text_summary(value), text_summary(display), text_summary(shown)))
        for depth, node in enumerate(nodes):
            write("  parent[{}] {}".format(depth, values(node)))

        for name, value in sorted(vars(hud).items()):
            name_lower = name.lower()
            if "scroll" not in name_lower and "input" not in name_lower:
                continue
            if value in (None, label):
                continue
            if frames.attr(value, "height") is not frames.MISSING:
                write("  hud.{} {}".format(name, values(value)))

    def watch(target, event):
        @ctx.wrap(target, safe=True)
        def wrapped(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                value = kwargs.get("value")
                if value is None and event == "update_display_text" and len(args) >= 2:
                    value = args[1]
                snapshot(self, event, value)
            except Exception:
                ctx.log_exc("message viewport probe: cannot snapshot {}".format(event))
            return result
        return wrapped

    @ctx.wrap("__main__:InstantaleApp.add_text_display", required=False, safe=True)
    def add_text_display(orig, self, dt, context, index=-1):
        before = frames.attr(self, "is_adding_text")
        result = orig(self, dt, context, index)
        state["stream_calls"] += 1
        after = frames.attr(self, "is_adding_text")
        if (state["stream_events"] < MAX_STREAM_EVENTS
                and (index == -1 or after is False
                     or state["stream_calls"] % TEXT_SAMPLE_CHARS == 0)):
            state["stream_events"] += 1
            write("stream: context={} index={} calls={} is_adding_text {} -> {}"
                  " queue={}".format(
                      text_summary(context), index, state["stream_calls"],
                      before, after, queue_snapshot(self)))
        return result

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, context, *args, **kwargs):
        before = queue_snapshot(self)
        result = orig(self, context, *args, **kwargs)
        flow("add_text", "context={} before={} after={}".format(
            text_summary(context), before, queue_snapshot(self)))
        return result

    @ctx.wrap("__main__:InstantaleApp.add_text_immediately", required=False, safe=True)
    def add_text_immediately(orig, self, content, *args, **kwargs):
        before = queue_snapshot(self)
        result = orig(self, content, *args, **kwargs)
        flow("add_text_immediately", "content={} before={} after={}".format(
            text_summary(content), before, queue_snapshot(self)))
        if isinstance(content, str):
            schedule_batch_snapshots(self, content)
        return result

    @ctx.wrap("__main__:InstantaleApp.wait_for_add_text", required=False, safe=True)
    def wait_for_add_text(orig, self, *args, **kwargs):
        started = time.monotonic()
        entry = queue_snapshot(self)
        result = orig(self, *args, **kwargs)
        flow("wait_for_add_text", "{:.2f}s entry={} exit={}".format(
            time.monotonic() - started, entry, queue_snapshot(self)))
        return result

    # 毎フレーム呼ばれる。**変わったときだけ**書く（そうしないと上限を一瞬で使い切る）。
    @ctx.wrap("__main__:InstantaleApp.process_text_queue", required=False, safe=True)
    def process_text_queue(orig, self, dt, *args, **kwargs):
        before = queue_snapshot(self)
        result = orig(self, dt, *args, **kwargs)
        after = queue_snapshot(self)
        if after != before or after != state["queue"]:
            state["queue"] = after
            flow("process_text_queue", "{} -> {}".format(before, after))
        return result

    watch("scripts.hud.new_hud:InstanTaleHUD.update_text_display_size",
          "update_text_display_size")
    watch("scripts.hud.new_hud:InstanTaleHUD._on_scroll_resize", "_on_scroll_resize")
    watch("scripts.hud.new_hud:InstanTaleHUD._on_text_input_layout_resize",
          "_on_text_input_layout_resize")
    watch("scripts.hud.new_hud:InstanTaleHUD.update_display_text", "update_display_text")

    ctx.log("message viewport probe installed; measurements go to out/{}".format(
        LOG_BASENAME))
