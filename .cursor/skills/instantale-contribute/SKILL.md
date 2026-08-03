---
name: instantale-contribute
description: 自作 MOD や修正を fork へ push し、本家 Flossian/InstantaleModLoader への PR を準備する。変更を fork に反映したいとき、PR を出したいときに使う。
disable-model-invocation: true
---

# fork への反映と PR

## リモート

| 名前 | 先 |
|---|---|
| `origin` | `tomotomo-y/InstantaleModLoader`（fork） |
| `upstream` | `Flossian/InstantaleModLoader`（本家） |

## 日常の反映（PR にしない）

`personal/*` にそのまま commit して push する。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .cursor/skills/instantale-dev-setup/scripts/verify.ps1
git add <変更したファイル>
git commit -m "feat: ..."
git push origin personal/v1.1
```

## PR を出す

### 鉄則: `feat/*` は `upstream/main` から作る

`personal/*` から分岐すると、自分の設定・MOD の並び・`.cursor/` まで PR に入る。

```powershell
git fetch upstream
git switch -c feat/<mod-name> upstream/main
```

### 載せるもの・載せないもの

| 載せる | 載せない |
|---|---|
| `runtime/mods/<自分の MOD>/` | `out/` `settings/`（`.gitignore` 済み） |
| `tools/test_<自分の MOD>.py` | `__pycache__` / `*.pyc` |
| `docs/` の該当節への追記 | `.cursor/`（personal 限定） |
| `load_order.json` への**自分の MOD の行だけ** | personal 側の並べ替え・`disabled` |

`personal/*` から特定のファイルだけ持ってくる:

```powershell
git checkout personal/v1.1 -- runtime/mods/1308_companion_travel tools/test_companion_travel.py
```

### 番号を確認する

本家は番号を先に使うことがある。PR に出す前に `upstream/main` の
`runtime/mods/` と自分の番号が重なっていないか見る。重なっていたら自分側を
+1000 帯へ動かす（手順は `instantale-upstream-sync` を参照）。
未マージの fork 専用だけ +1000（`1114_` / `1308_` など）。本家マージ後は本家番号に揃える。

テストは番号なしで MOD を引く形（`find_mod("_suffix")`）にしておく。

### 検証してから出す

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .cursor/skills/instantale-dev-setup/scripts/verify.ps1
git push -u origin HEAD
gh pr create --repo Flossian/InstantaleModLoader --title "..." --body "..."
```

PR は本家に通知が行く。出す前に内容を確定させること。

### PR の粒度

1 PR = 1 つの MOD、または 1 つの修正。バグ修正と機能追加は分ける。
機能追加はゲームバランスを変えるので、本家が採るかどうかの判断が別になる。

## コミットメッセージ

Conventional Commits・日本語・原子的。

```
feat: NPCを連れて歩く MOD を追加

雇用（パーティ）を使わずに会話から同行を持ちかける。施設間は無条件に、
土地を跨ぐときは本人の判断で付いて来る。
```

| 接頭辞 | 使いどころ |
|---|---|
| `feat:` | 新しい MOD・新しい機能 |
| `fix:` | バグ修正 |
| `refactor:` | 挙動を変えない整理（番号の振り直しなど） |
| `docs:` | ドキュメントだけ |
