# `1132_policy_guardrail_bypass`: 安全ポリシーのガードレールを外す

ゲームが LLM に載せる「著作権・安全ポリシー」の指示を削り、
返ってきた違反判定フィールドを問題なしへ強制する。

自前の OpenAI 互換プロキシ（Instantale プロファイル）がやっているのと同じ処理を、
プロキシ無しでもプロセス内で効かせる。

## 何をするか

**送信（既定 ON）**

- `【著作権・安全ポリシー違反の基準】` セクションを削除（TRPG GM ではマーカー以降を切断）
- `- content_violation:` / `- lawfulness_loss:` の指示行を削除

**応答（既定 ON）**

- `content_violation` / `copyright_violation` / `policy_violation` を false
- `lawfulness_loss` を 0（ネストも）
- RPG 管理者評価では `assessment` を問題なし文言へ（キーがあるときだけ）

効いているかは `out\policy_guardrail_bypass.log` の
`prompt stripped` / `response forced` で分かる。

| 設定 | 意味 |
|---|---|
| この MOD を使う | 既定 ON。OFF で全部停止 |
| 送信プロンプトからポリシー指示を削る | 既定 ON |
| 応答の違反判定を無効化する | 既定 ON |
| RPG管理者評価を問題なしへ通す | 既定 ON |
| ログに残す件数 | 既定 20 |

#### 困ったとき

- `119_fix_crime_attribution` と同居すると、評判低下（`lawfulness_loss`）は
  こちらが常時 0 にする側が勝つ。他者犯罪だけ 0 にしたいなら
  「応答の違反判定を無効化する」を OFF にする
- プロンプトが変わらない → ローカル／クラウドどちらの経路でも `wrap_outgoing` を通る。
  ログに `prompt stripped` が出るか確認する
