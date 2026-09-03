# -*- coding: utf-8 -*-
"""Ollama の OpenAI 互換 API で思考モードを切る。

ゲームの「任意 OpenAI 互換サーバー」経路は `openai` SDK 経由で
`/v1/chat/completions` を叩く。Ollama はこの経路で思考対応モデルの
思考を**既定 ON**にし、`think: false`（ネイティブ `/api/chat` 用）は
受け取らない。切るには `reasoning_effort: "none"` が要る。

ゲーム本体はこれを付けない。自前プロキシならネイティブ API へ寄せて
`think: false` で切れるが、プロキシ無しで同じ効果を出すのがこの MOD。

## どこに仕掛けるか

    openai._base_client:SyncAPIClient.post

`900_cloud_model_override` と同点。chat / responses のどちらも最後はここを通る。
`chat/completions` の body だけを浅い写しで書き換え、呼び出し側の dict は触らない。
responses 経路や body が無い呼び出しは素通し。

## 割り切り

  * 効くのは Ollama の `/v1`（および同等に `reasoning_effort` を見る互換サーバ）。
    公式 OpenAI の responses 経路には手を出さない
  * 既に `reasoning_effort` や `reasoning.effort` が付いている要求は上書きしない
    （明示指定を尊重する）
  * ローカル llama.cpp（`LlamaCppClient`）経路は `openai` を通らないので無関係
"""

# 思考を切るか。OFF にするとフックごと仕掛けない。
# **`mod.json` の "default" と揃えること**（`tools/check_mods.py` が
# AST で突き合わせる。TECH.md §3.8.3）。
ENABLED = True

# 書き換えた回数をログに出す上限。毎回出すと埋まるので先頭だけ。
LOG_LIMIT = 5

#: openai の SDK で、全リクエストが最後に通る1点。
POST_TARGET = "openai._base_client:SyncAPIClient.post"

#: Ollama の OpenAI 互換で思考 OFF を意味する値。
EFFORT_NONE = "none"

CHAT_MARK = "chat/completions"


def is_chat_completions(path):
    """この path が chat/completions か。クエリ付きでも拾う。"""
    if not isinstance(path, str):
        return False
    base = path.split("?", 1)[0]
    return base.rstrip("/").endswith(CHAT_MARK)


def already_has_effort(body):
    """呼び出し側が既に努力度を指定しているか。"""
    if not isinstance(body, dict):
        return False
    if "reasoning_effort" in body:
        return True
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and "effort" in reasoning:
        return True
    return False


def with_effort_none(body):
    """浅い写しに `reasoning_effort: none` を足す。元の dict は変えない。"""
    return dict(body, reasoning_effort=EFFORT_NONE)


def apply(ctx):
    if not ENABLED:
        ctx.log("ollama disable thinking: off")
        return

    state = {"patched": 0, "skipped": 0}

    # required=False: ローカル llama.cpp だけで遊んでいる間は openai が無い。
    # モジュールが現れた時点でローダが当て直す。
    # safe=True: ここが壊れても素の送信に落とす。
    @ctx.wrap(POST_TARGET, required=False, safe=True, alias_scan=False)
    def post(orig, self, path=None, *args, **kwargs):
        body = kwargs.get("body")
        if not (is_chat_completions(path) and isinstance(body, dict)):
            return orig(self, path, *args, **kwargs)

        if already_has_effort(body):
            state["skipped"] += 1
            return orig(self, path, *args, **kwargs)

        kwargs = dict(kwargs, body=with_effort_none(body))
        state["patched"] += 1
        if state["patched"] <= LOG_LIMIT:
            ctx.log("ollama disable thinking: reasoning_effort=none ({})".format(
                path))
        return orig(self, path, *args, **kwargs)

    ctx.log("ollama disable thinking: installed")
