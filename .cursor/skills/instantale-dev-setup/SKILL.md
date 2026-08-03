---
name: instantale-dev-setup
description: Instantale MOD 開発の環境前提・編集ループ・検証手順を示す。MOD を書き始めるとき、環境構築や検証コマンドを聞かれたとき、テストや静的検査を通したいときに使う。
disable-model-invocation: true
---

# Instantale MOD 開発

## 作業ツリー

リポジトリのクローンをそのままプレイ環境として使う。`InstantaleModLoader.bat` は
リポジトリ直下にあり、`out/` と `settings/` は `.gitignore` 済みなので、
**ブランチ切り替えやマージでプレイ状態は消えない**。

| ブランチ | 用途 |
|---|---|
| `personal/*` | 日常の開発とプレイ。本家の取り込み先 |
| `feat/*` | 本家への PR 用。**必ず `upstream/main` から作る** |

`.cursor/` は `personal/*` にだけ置く。`feat/*` へ持ち込まない。

## 環境の決まり

| 決まり | 理由 |
|---|---|
| ゲーム側は CPython 3.10。`runtime/` に 3.11 以降の構文を書かない | 注入した瞬間に落ちる。手元の python は 3.13 なので `compileall` では捕まらず、`check_mods.py` が `ast` の `feature_version=(3,10)` で弾く |
| `.bat` は ASCII のみ | その時のコードページで読まれるため、日本語を入れると環境によって解析が壊れる |
| ツールから MOD を読むときは番号を書かない | `find_mod("_companion_travel")` のように番号を除いた名前で引く。番号を振り直しても壊れない |
| MOD が書いてよいのは `out/` だけ | `runtime/mods/` は読む専用。設定は `settings/mod_settings.json`（ローダが管理） |

64bit の Python 3.13 が要る。32bit では注入できない。

## 編集ループ

```powershell
# 1. MOD を編集する
# 2. ゲームは起動したまま、注入し直す
python tools/injector.py

# 剥がす
python tools/injector.py --unload
```

`boot()` が `instantale_modloader` を `sys.modules` から落として読み直すので、
層は積み上がらない。ゲームを再起動するたびに注入し直すこと。

`on_ready` に預けた一回きりの初期化は注入し直しても走らない。その初期化自体を
直しているときだけ `ctx.on_ready(fn, force=True)` を一時的に使う。配布する MOD に
`force=True` を残さない。

## 検証

コミットや push の前に必ず通す。CI と同じ検査を同じ順で回す。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .cursor/skills/instantale-dev-setup/scripts/verify.ps1
```

内訳は `compileall runtime tools` → `tools/check_mods.py` → 全 `tools/test_*.py`。
`check_mods.py` が捕まえるのは構文では出ないずれ:

- `@ctx.wrap` の対象名と実際の引数の並びの食い違い
- `mod.json` の `entry` が指すファイルの不在
- `load_order.json` との食い違い、`after` / `before` の循環
- `settings` の既定値とコード内の定数のずれ

`MISMATCH` が出たら直す。`note` の欠落は表示専用なので後回しでよい。

## 新しい MOD を作る

`runtime/mods/_template/` をコピーして名前を付ける。先頭が `_` のフォルダは
読み込まれない。番号帯は分類のためだけ（`0xx` 調査 / `1xx` 修正 / `2xx` 計測 /
`3xx` 追加）で、適用順は `load_order.json` と `mod.json` の `after` / `before` が決める。

**未マージの fork 専用 MOD だけ番号を +1000 する**（例: `1114_` / `1308_`）。
本家にマージされたら本家の番号に従う。本家同梱を直すときは番号を動かさない。

コピー直後は `load_order.json` に載っていないので、GUI の一覧から並べるか
`"order"` に自分で足す。`check_mods.py` が「記載の無い MOD」として報告する。

表示名には長さの上限がある（`name.en` は半角 30、`name.ja` は全角 12 相当）。
超えると `test_patch_registry.py` が落ちる。長い説明は `description` に置く。

## 状態ファイル

| パス | 中身 |
|---|---|
| `settings/gui.json` | ゲームの場所・ウィンドウ位置 |
| `settings/mod_settings.json` | GUI から変えた MOD 設定。**MOD のフォルダ名がキー**なので、番号を振り直す前に見ておく |
| `out/*.log` | 注入ごとに世代交代する |
| `out/companions.json` / `out/npc_profiles/<世界名>.json` など | MOD が持つ永続データ。ログではないので世代交代しない |

いずれも `.gitignore` 済み。commit しない。
