---
name: instantale-mod-conventions
description: Instantale の MOD・パッチ・オフラインテストを書く／直すときに使う。texture_update、apply 世代を跨ぐ状態、ボタン印、find_mod、番号帯、セーブに独自キーを足さない規則。
---

# Instantale MOD 実装規約

本家 `docs/TECH.md` §6 / §3.4 / §2.4 と、レビューで確定した一般則。
短い強制リストは `.cursor/rules/instantale-mods.mdc`。ここは詳細。

## 必須ルール

| 規則 | 理由 |
|---|---|
| `texture_update()` を自分から呼ばない | Kivy はテキスト代入時に次フレームへ作り直しを予約する。MOD が重ねて呼ぶと二度手間になり、代金は**フレーム時間**に乗る。フック内の `orig` 計測には出ない（TECH.md §6.2） |
| 表示中の**文字列**で描画先を探さない | 他 MOD が文字列を差し替えると探索が空振りする。一度分かった属性名で引く（例: `hud.text_display`） |
| 残骸掃除は `ui.Screen.prune_stale` / `marked_by_a_mod` | 文言一致だけで「依頼を受ける」等に印を刻むと、他 MOD やゲームのボタンを横取りする。`JustSetButtonToNormalPhase` と他印の有無も見ること |
| `apply()` を跨ぐ状態は `sys` の store に載せる | `apply()` は再注入と遅延当て直しで最大8回走る。世代ごとに `Queue` / `Lock` / worker を作り直すと二重起動や排他抜けが起きる（TECH.md §3.4） |
| テストは `find_mod("_suffix")` | 番号を振り直しても壊れない（TECH.md §2.4）。`os.path.join(..., "117_...", ...)` 禁止 |
| セーブ構造に独自キーを足さない | 焼かれて残り方が不定。永続は `out/` のみ |
| 印キーは他 MOD と分ける | `mod_` で始まるキー。他の掃除が「印なし」と誤認しないようにする |

## `apply()` 世代を跨ぐ状態

```python
STATE_STORE_ATTR = "__instantale_<mod>_store__"

def apply(ctx):
    store = getattr(sys, STATE_STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "state": {...},
            "jobs": queue.Queue(),
            "data_lock": threading.RLock(),
        }
        setattr(sys, STATE_STORE_ATTR, store)
    state = store["state"]
    jobs = store["jobs"]
    data_lock = store["data_lock"]
```

`apply()` のたびに `state = {...}` / 新しい `Queue()` を作るな。
可能なら「`apply()` を2回呼んでも worker が1本」のテストを足せ。

## テストの引き方

```python
def find_mod(suffix):
    mods = os.path.join(ROOT, "runtime", "mods")
    for name in sorted(os.listdir(mods)):
        if name.endswith(suffix) and not name.startswith("_"):
            return os.path.join(mods, name)
    raise FileNotFoundError(suffix)

MOD_PATH = find_mod("_message_text_integrity")  # 番号を書かない
```

## 番号帯

| 場所 | 採番 |
|---|---|
| 本家 PR（`feat/*`） | 本家 `upstream/main` の空き **3 桁**（`1xx` 修正 / `3xx` 追加）。+1000 のまま出さない |
| `personal/*` の未マージ固有 | 本家帯と共存するため **+1000** 可（例: `1114_` / `1308_`） |
| 本家にマージされたあと | 本家が振った番号に揃える。取り込み時に旧 +1000 フォルダは消す |

分類帯そのもの（`0xx` / `1xx` / `2xx` / `3xx`）は TECH.md §3.2.2 に従う。

## 関連

- 開発ループ・verify: skill `instantale-dev-setup`
- 本家 PR: skill `instantale-contribute`
- 本家取り込み: skill `instantale-upstream-sync`
