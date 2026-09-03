# `1131_ollama_disable_thinking`: Ollama（OpenAI互換）の思考モードを切る

ゲームを Ollama の OpenAI 互換 API（`/v1/chat/completions`）に直接繋いでいると、
思考対応モデルは**思考が既定で ON**になり、応答が遅くなる。
この MOD は送信直前の body に `reasoning_effort: "none"` を足して切る。

自前の OpenAI 互換プロキシ経由なら、プロキシ側が Ollama ネイティブ API の
`think: false` で同じ効果を出せる。プロキシ無しで同じことをするのがこの MOD。

効いているかは `out\modloader.log` に
`ollama disable thinking: reasoning_effort=none (...)` が出ることで分かる。
ローカル llama.cpp（ゲーム同梱のサイドカー）や、公式クラウド API の
responses 経路では何もしない。

| 設定 | 意味 |
|---|---|
| 思考モードを切る | 既定 ON。OFF にするとフックごと外す |
| 書き換えをログに出す回数 | 既定 5。0 で何も出さない |

#### 困ったとき

- ログに一行も出ない → ゲームが任意 OpenAI 互換ではなくローカル llama.cpp か、
  別プロバイダを使っている。設定画面の接続先を確認する
- 付与しても遅い → モデル／Ollama の版が `reasoning_effort` を見ない可能性がある。
  その場合はプロキシ経由（ネイティブ `think: false`）の方が確実
