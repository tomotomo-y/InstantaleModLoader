# -*- coding: utf-8 -*-
"""犯罪主体の判定経路を読み取り専用で計測する。

`111_fix_crime_attribution` の外側から、会話ログ付き自由行動、LLM の二つの
判定プロンプト、manager 戻り値、最終的な「犯罪者」表示を一本のログへ記録する。
値は一切変更しない。
"""

import datetime

from instantale_modloader.frames import repr_value


LOG_BASENAME = "crime_attribution.log"

FACILITATOR_TARGETS = (
    "master_ai_facilitator",
    "master_ai_facilitator_from_conversation",
    "master_ai_facilitator_in_quest",
    "master_ai_faciltiator_from_conversation_in_quest",
)

SUMMARIZER_TARGETS = (
    "master_ai_process_summarizer",
    "master_ai_process_summarizer_in_conversation",
    "master_ai_process_summarizer_in_conversation_in_quest",
    "master_ai_process_summarizer_in_quest",
    "master_ai_process_summarizer_with_no_recipients",
)

MANAGER_NAMES = frozenset(FACILITATOR_TARGETS + SUMMARIZER_TARGETS)
MAX_PREVIEW = 240


def _get(container, name):
    if isinstance(container, dict):
        return container.get(name)
    return getattr(container, name, None)


def _preview(value, limit=MAX_PREVIEW):
    text = value if isinstance(value, str) else repr_value(value)
    return text.replace("\r", "\\r").replace("\n", "\\n")[:limit]


def _shape(value):
    if isinstance(value, dict):
        return "dict{" + ", ".join(sorted(str(k) for k in value)[:10]) + "}"
    if isinstance(value, (list, tuple)):
        return "{}[{}]".format(type(value).__name__, len(value))
    try:
        fields = sorted(vars(value))[:10]
    except Exception:
        fields = []
    return "{}({})".format(type(value).__name__, ", ".join(fields))


def _result_fields(result):
    process = _get(result, "process")
    process_types = []
    if isinstance(process, (list, tuple)):
        for item in process:
            process_types.append(_get(item, "type"))
    return (
        "shape={} think={!r} summary={!r} process={} lawfulness_loss={!r}"
        .format(
            _shape(result),
            _preview(_get(result, "think")),
            _preview(_get(result, "summary")),
            process_types,
            _get(result, "lawfulness_loss"),
        )
    )


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    sequence = {"value": 0}

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("crime attribution probe: log write failed")

    def next_id(label):
        sequence["value"] += 1
        value = "{}-{:04d}".format(label, sequence["value"])
        return value

    @ctx.wrap("__main__:FreeInputStart.method", required=False)
    def free_input_method(orig, self, choice_text, *args, **kwargs):
        call_id = next_id("free")
        size = len(choice_text) if isinstance(choice_text, (str, list, tuple)) else None
        write("{} FreeInputStart.method choice_type={} size={} last={}".format(
            call_id, type(choice_text).__name__, size,
            _preview(choice_text[-1] if isinstance(choice_text, (list, tuple)) and choice_text
                     else choice_text)))
        return orig(self, choice_text, *args, **kwargs)

    @ctx.wrap(
        "scripts.llm.request_llm_inference_llama_cpp_completion:send_request",
        required=False,
    )
    def send_request(orig, manager_name, message, structure, *args, **kwargs):
        if manager_name not in MANAGER_NAMES:
            return orig(manager_name, message, structure, *args, **kwargs)
        call_id = next_id("request")
        write("{} send_request manager={!r} message_shape={} structure={}".format(
            call_id, manager_name, _shape(message), _shape(structure)))
        result = orig(manager_name, message, structure, *args, **kwargs)
        write("{} send_request result {}".format(call_id, _result_fields(result)))
        return result

    @ctx.wrap("llama_cpp_runtime_completion:LlamaCppClient.chat", required=False)
    def chat(orig, self, model, messages, format=None, *args, **kwargs):
        contents = [
            message.get("content")
            for message in messages or ()
            if isinstance(message, dict) and isinstance(message.get("content"), str)
        ]
        blob = "\n".join(contents)
        fingerprints = []
        if "arrest_player" in blob:
            fingerprints.append("arrest_player")
        if "lawfulness_loss" in blob:
            fingerprints.append("lawfulness_loss")
        if not fingerprints:
            return orig(self, model, messages, format, *args, **kwargs)

        call_id = next_id("chat")
        write("{} chat fingerprints={} messages={} chars={} format={}".format(
            call_id, fingerprints, len(contents), len(blob), _shape(format)))
        result = orig(self, model, messages, format, *args, **kwargs)
        # ストリームを消費するとゲームの応答を奪うため、型と属性だけを見る。
        write("{} chat result_shape={}".format(call_id, _shape(result)))
        return result

    def wrap_manager(name):
        @ctx.wrap("scripts.llm.llm_manager:{}".format(name), required=False)
        def manager(orig, *args, **kwargs):
            call_id = next_id(name)
            write("{} enter args={} kwargs={}".format(
                call_id, len(args), sorted(kwargs)))
            result = orig(*args, **kwargs)
            write("{} return {}".format(call_id, _result_fields(result)))
            return result
        return manager

    for target_name in FACILITATOR_TARGETS + SUMMARIZER_TARGETS:
        wrap_manager(target_name)

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False)
    def add_text(orig, self, context, *args, **kwargs):
        if isinstance(context, str) and "犯罪者" in context:
            write("{} add_text CRIMINAL {!r}".format(next_id("display"), context))
        return orig(self, context, *args, **kwargs)

    @ctx.wrap("__main__:TrialStartManager.__init__", required=False)
    def trial_start_init(orig, self, app, charges, incident_details, *args, **kwargs):
        write("{} TrialStartManager charges={} incident={}".format(
            next_id("trial"), _preview(charges), _preview(incident_details)))
        return orig(self, app, charges, incident_details, *args, **kwargs)

    @ctx.wrap("__main__:ImprisonmentStartManager.__init__", required=False)
    def imprisonment_start_init(
            orig, self, app, imprisonment_years, charges, incident_details,
            *args, **kwargs):
        write("{} ImprisonmentStartManager years={!r} charges={} incident={}".format(
            next_id("prison"), imprisonment_years,
            _preview(charges), _preview(incident_details)))
        return orig(
            self, app, imprisonment_years, charges, incident_details,
            *args, **kwargs
        )

    ctx.log("crime attribution probe installed; log {}".format(log_path))
