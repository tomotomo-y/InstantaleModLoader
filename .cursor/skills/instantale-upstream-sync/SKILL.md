---
name: instantale-upstream-sync
description: 本家 Flossian/InstantaleModLoader の最新を fork の personal ブランチへ取り込む。本家がバージョンアップしたとき、upstream を取り込みたいとき、MOD の番号が本家と衝突したときに使う。
disable-model-invocation: true
---

# 本家の取り込み

## 取り込み先は `upstream/main`

タグではなく `upstream/main` を取る。本家はリリースノートに書いた変更をタグの後に
積むことがあり、タグだけ取ると「本家の今」に追いつけない。

```powershell
git fetch upstream --tags
git log --oneline v1.1.0..upstream/main   # タグとの差を見る
```

## 手順

```
- [ ] 1. 作業ツリーを綺麗にする（未 commit があれば先に片付ける）
- [ ] 2. personal ブランチで merge
- [ ] 3. 番号衝突を洗う
- [ ] 4. 衝突を解消する
- [ ] 5. verify.ps1 を通す
- [ ] 6. commit して push
```

### 1. 前提の確認

`out/` と `settings/` は `.gitignore` 済みなので merge では触られない。バックアップは
要らない。未 commit の変更だけ先に片付ける。

### 2. merge

```powershell
git switch personal/v1.1
git fetch upstream
git merge upstream/main
```

### 3. 番号衝突を洗う

**本家が自分と同じ番号を使い始めていないか必ず見る。** 過去に `111` と `306` を
本家に取られている。

```powershell
git diff --name-only --diff-filter=A HEAD@{1} upstream/main -- runtime/mods | Select-String "mod.json"
```

自分の MOD と番号が重なっていたら、**自分側を空き番号へ動かす**（本家の番号を残す）。

```powershell
git mv runtime/mods/111_fix_crime_attribution runtime/mods/113_fix_crime_attribution
```

移した後に追う場所:

| 場所 | 何を直すか |
|---|---|
| `runtime/mods/load_order.json` | 名前。並びは `after` / `before` を満たす位置に置く |
| 他 MOD の `mod.json` | `after` / `before` に旧番号が残っていないか |
| `runtime/mods/*/**.py` | 説明コメントの参照 |
| `tools/test_*.py` | **番号なしの `find_mod("_suffix")` に直す**。番号を直書きしない |
| `docs/README.md` / `docs/VERIFICATION.md` | 見出しと一覧の番号 |

`load_order.json` の並びが `after` / `before` と食い違うと `check_mods.py` が
`MISMATCH` を出す。正しい並びはこれで取れる:

```powershell
python -c "import sys,json; sys.path.insert(0,'runtime'); import instantale_modloader as l; print(json.dumps(l.discover()['order'], ensure_ascii=False, indent=1))"
```

### 4. 定番の衝突

| ファイル | 解消の仕方 |
|---|---|
| `runtime/mods/load_order.json` | 本家の並びを土台にし、自分の MOD を制約を満たす位置へ足す |
| `docs/*.md` | **本家版を採用**（`git checkout --theirs`）し、自分の MOD の記述を該当節へ載せ直す。テキストマージしない |
| 改変した同梱 MOD | 本家の変更を取り込み、自分の変更を残す。両方の意図を確認する |

### 5. 検証

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .cursor/skills/instantale-dev-setup/scripts/verify.ps1
```

**本家が追加したテストが自分の改変で落ちることがある。** 同梱 MOD に手を入れて
いる場合はここで出る。落ちたら改変側を直す（本家のテストを緩めない）。

### 6. push

```powershell
git push origin personal/v1.1
```

fork 側でも CI が走る。緑を確認する。

## 取り込み後の確認

- GUI を起動して `game_path` が残っていること、本家の新 MOD と自分の MOD が
  両方一覧に出ること
- 注入して `out/modloader.log` の `boot complete:` の件数
- 自分の MOD の永続データ（`out/*.json`）が引き継がれていること
