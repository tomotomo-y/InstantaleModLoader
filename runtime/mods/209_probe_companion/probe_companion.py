# -*- coding: utf-8 -*-
"""計測: 土地移動の経路を写し取る（読み取り専用）。

`308_companion_travel`（パーティを使わずに NPC を連れて歩く）を書くために、
実データで潰せなかった3点だけを見る。好感度・関係性の文面と、パーティ外の NPC
にも会話の記録が溜まることは `output_data/` の実ログで確認済みなので、ここでは
確かめ直さない。

## 1. 徒歩／馬車ボタンの `PhaseSpec`

`306_` は**この押下を横取りする**。押されたボタンの `cls_name` と `args` を
そのまま再発行することで、`AreaMoveManager(app, target_area_id, mode)` の引数の
意味を知らずに移動を起こせる（`301_` が `QuestChoiceManager` の引数を推測して
落とした反省。GAME.md §2.2）。**その値がここで確定していなければ横取りは書けない。**

実機観察（ユーザー報告・2026-07-30）では画面はこう流れる:

```
「他の土地へ行く」                     -> DisplayAreaMoveChoice
「嘆きの村」/「断罪の都市」/「やめる」
「徒歩(3か月)」/「馬車(1000G)」          -> ここを押すと同行拒否判定が入る
「・」「・・」「・・・」「辿り着いた。」      -> show_loading_text
到着 -> その土地の移動先候補が並ぶ
```

`mode` を引数に持つのは `AreaMoveManager` だけなので、徒歩／馬車のボタンは
このクラスの spec を持っているはず ― **はず、で書かない。**

## 2. `AreaMoveRestriction` が何のゲートか

土地移動に関わるクラスは4つある（`out/recon/targets.txt`）。

```
DisplayAreaMoveChoice(app)              execute / update_button_display
AreaMoveCofirmation(app, target_area_id)  execute / update_button_display
AreaMoveRestriction(app, target_area_id)  execute / update_button_display
AreaMoveManager(app, target_area_id, mode)  execute / method_1 / show_loading_text
```

拒否のセリフを作っているのは `llm_manager:area_move_rejector(character_life_log,
player, character_instance, worldview)` で、これも包む。**どのクラスがそれを
呼んでいるのか**が分かれば、`306_` が横取りすべき地点が確定する。

## 3. 到着が確定する瞬間

`306_` は同行者を `move_npc_to_facility(character_id, character_instance,
target_facility, target_node=None)` で移動先へ置く。**置き先の Facility が
揃っている瞬間**を知らないと、まだ出発地を指している間に置いてしまう。
`method_1` / `execute` の前後で `player.location` / `current_node` /
`current_area` を写し取り、どこで切り替わるかを見る。

この mod は**観測しかしない**。値は変えず、記録に失敗しても本体は必ず呼ぶ。
"""

import datetime

from instantale_modloader import frames, ui

LOG_BASENAME = "companion_probe.log"

# ボタン押下を何件まで記録するか。徒歩／馬車の spec が1回でも出れば目的は
# 果たすが、そこへ至る画面も一緒に読めた方が切り分けが速いので余裕を持たせる。
MAX_PRESSES = 300

# 選択肢の顔ぶれを何件まで記録するか。署名が変わったときだけ書くので、
# 実際にはこれよりずっと少ない。
MAX_SCREENS = 200

# 土地移動に関わるクラス（`out/recon/targets.txt` より）。
AREA_MOVE_CLASSES = ("DisplayAreaMoveChoice", "AreaMoveCofirmation",
                     "AreaMoveRestriction", "AreaMoveManager")


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    state = {"presses": 0, "screens": 0, "last_screen": None}

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            # 記録のせいでゲームを落とさない。ここは常に握り潰す。
            ctx.log_exc("companion probe: write failed")

    def short(value, limit=60):
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        value = value.strip()
        return value if len(value) <= limit else value[:limit] + "…"

    # ------------------------------------------------------------ 現在地
    def describe_facility(facility):
        """施設を1行に。`move_npc_to_facility` に渡せる形かどうかを見る。"""
        if facility is None:
            return "None"
        return "{}(id={!r} name={!r} type={!r})".format(
            type(facility).__name__,
            frames.repr_value(frames.attr(facility, "id")),
            short(frames.attr(facility, "name"), 30),
            ui.facility_type_of(facility))

    def where(app, label):
        """いまプレイヤーがどこに居るか。**到着確定の判定はこの差分で行う。**

        `player.current_area` はエリアのオブジェクトとは限らず id の文字列の
        こともある（GAME.md §2.7）ので、生の値と引き当てた結果の両方を出す。
        """
        player = getattr(app, "player", None) if app is not None else None
        if player is None:
            write("  {}: no player".format(label))
            return
        raw_area = frames.attr(player, "current_area")
        area = ui.current_area(app)
        node = frames.attr(player, "current_node")
        write("  {}: location={} node={} area_raw={} area={} area_id={!r}".format(
            label,
            describe_facility(frames.attr(player, "location")),
            frames.repr_value(frames.attr(node, "id")
                              if node is not frames.MISSING else node),
            frames.repr_value(raw_area),
            type(area).__name__ if area is not None else "None",
            ui.area_id_of(area)))

    # ------------------------------------------------------------ 選択肢
    def dump_buttons(app, label):
        """いま並んでいる選択肢を spec ごと書き出す。

        **表示文字列ではなく `cls_name` と `args` が要る。** 横取りの実装は
        この値の上に成り立つ。
        """
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, (list, tuple)):
            write("  {}: app.buttons is {}".format(label, type(buttons).__name__))
            return
        write("  {}: {} button(s)".format(label, len(buttons)))
        for index, entry in enumerate(buttons):
            write("    [{}] text={!r} cls={!r} args={!r}".format(
                index, short((entry or {}).get("text") if isinstance(entry, dict)
                             else entry, 40),
                ui.spec_cls_name(entry), ui.spec_args(entry)))

    def trace_screen(app):
        """選択肢の顔ぶれが変わったときだけ1行残す。

        `refresh_choice_buttons` は頻繁に呼ばれるので、署名が同じなら書かない
        （`302_` と同じ考え）。
        """
        if state["screens"] >= MAX_SCREENS:
            return
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, (list, tuple)):
            return
        signature = tuple((ui.spec_cls_name(entry), tuple(ui.spec_args(entry) or ()))
                          for entry in buttons)
        if signature == state["last_screen"]:
            return
        state["last_screen"] = signature
        state["screens"] += 1
        write("screen changed:")
        dump_buttons(app, "buttons")

    # ================================================================ 押下
    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """押されたボタンの spec を記録する。**値は変えず必ず素通しする。**

        `306_` が横取りする対象を確定させるための、この mod の主目的。
        押された添字は `display_button_map` で引き直す（`ui.pressed_entry`）。
        """
        try:
            if state["presses"] < MAX_PRESSES:
                state["presses"] += 1
                entry = ui.pressed_entry(self, button_index)
                cls_name = ui.spec_cls_name(entry)
                write("press[{}] index={} text={!r} cls={!r} args={!r}".format(
                    state["presses"], button_index,
                    short((entry or {}).get("text") if isinstance(entry, dict)
                          else entry, 40),
                    cls_name, ui.spec_args(entry)))
                if cls_name in AREA_MOVE_CLASSES:
                    # 土地移動に入る押下。この前後が `306_` の作業場になる。
                    write("  ^^ area move press")
                    where(self, "before")
        except Exception:
            ctx.log_exc("companion probe: cannot record the press")
        return orig(self, button_index, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        result = orig(self, reset_page, *args, **kwargs)
        try:
            trace_screen(self)
        except Exception:
            ctx.log_exc("companion probe: cannot trace the screen")
        return result

    # ================================================== 土地移動の4クラス
    def watch_init(cls_name):
        """`__init__` を包んで引数だけ記録する。発火順を見るのが目的。"""

        @ctx.wrap("__main__:{}.__init__".format(cls_name), required=False)
        def init(orig, self, app, *args, **kwargs):
            try:
                write("{}({})".format(
                    cls_name,
                    ", ".join([repr(value) for value in args]
                              + ["{}={!r}".format(key, value)
                                 for key, value in sorted(kwargs.items())])))
            except Exception:
                ctx.log_exc("companion probe: cannot record {}".format(cls_name))
            return orig(self, app, *args, **kwargs)

        return init

    for _name in AREA_MOVE_CLASSES:
        watch_init(_name)

    @ctx.wrap("__main__:AreaMoveManager.execute", required=False)
    def area_move_execute(orig, self, choice_text, *args, **kwargs):
        """移動そのもの。**前後で現在地を写す**のがこの mod の目的の3つ目。"""
        write("=" * 78)
        write("AreaMoveManager.execute({!r})".format(short(choice_text, 40)))
        app = getattr(self, "app", None)
        try:
            write("  attrs: {}".format(sorted(vars(self))))
        except Exception:
            write("  attrs: <vars() unavailable>")
        where(app, "before execute")
        result = orig(self, choice_text, *args, **kwargs)
        where(app, "after execute")
        dump_buttons(app, "after execute")
        return result

    @ctx.wrap("__main__:AreaMoveManager.method_1", required=False)
    def area_move_method_1(orig, self, *args, **kwargs):
        app = getattr(self, "app", None)
        where(app, "before method_1")
        result = orig(self, *args, **kwargs)
        where(app, "after method_1")
        return result

    @ctx.wrap("__main__:AreaMoveManager.show_loading_text", required=False)
    def area_move_loading(orig, self, *args, **kwargs):
        write("AreaMoveManager.show_loading_text()")
        return orig(self, *args, **kwargs)

    # ============================================ ゲーム自身の同行拒否判定
    @ctx.wrap("scripts.llm.llm_manager:area_move_rejector", required=False)
    def area_move_rejector(orig, character_life_log, player, character_instance,
                           worldview, *args, **kwargs):
        """パーティ NPC が土地移動に付いて来ないときの拒否のセリフ。

        `output_data/unknown/unknown/area_move_rejector/` の実ログから、
        「関係性が深くないために拒否されるべき」という前提でセリフを作らせて
        いることが分かっている。**誰が呼んでいるのか**をここで確定させる。
        """
        write("-" * 78)
        write("area_move_rejector(character={!r}) from {}".format(
            short(frames.attr(character_instance, "name"), 30), frames.caller()))
        result = orig(character_life_log, player, character_instance, worldview,
                      *args, **kwargs)
        write("area_move_rejector -> {}".format(frames.repr_value(result)))
        return result

    # ============================================ 既存の雇用経路（衝突確認）
    @ctx.wrap("__main__:ConversationEmployManager.__init__", required=False)
    def conversation_employ_init(orig, self, app, employ_price, *args, **kwargs):
        """雇用は自由入力から入る（実ログで確認）。**ボタンではない**ことの裏取り。

        `306_` の「同行を持ちかける」ボタンが既存 UI と衝突しないかを見る。
        """
        write("ConversationEmployManager(employ_price={!r}, args={!r}, kwargs={!r})"
              .format(employ_price, args, sorted(kwargs)))
        return orig(self, app, employ_price, *args, **kwargs)

    ctx.log("companion probe installed; area move goes to out/{}".format(LOG_BASENAME))
