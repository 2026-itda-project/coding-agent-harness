"""Claude Messages API transport. Stdlib only, same pattern as the SnowChat transport."""

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from app.llm.errors import AnalysisError
from app.llm.snowchat import NoRedirect

ENDPOINT = "https://api.anthropic.com/v1/messages"
# USD per 1M input/output tokens (Claude model overview, checked 2026-10-14).
PRICES = {"claude-sonnet-5-5": (2, 10), "claude-opus-5-5": (4, 20)}


def model_for(role):
    """role: 'sonnet' or 'opus'. IDs come from the environment, never a silent substitute."""
    model = os.getenv("SONNET_MODEL" if role == "sonnet" else "OPUS_MODEL", "").strip()
    if not model:
        raise AnalysisError("invalid_config", f"{role.upper()}_MODEL이 설정되지 않았습니다.", 503)
    return model


def cost(model, usage):
    if model not in PRICES:
        raise AnalysisError("invalid_config", "단가가 등록되지 않은 모델입니다.", 503)
    if usage.get("cache_creation_input_tokens") or usage.get("cache_read_input_tokens"):
        raise AnalysisError("gateway_error", "예상하지 않은 캐시 과금 응답입니다.")
    incoming, outgoing = PRICES[model]
    return (usage["input_tokens"] * incoming + usage["output_tokens"] * outgoing) / 1_000_000


def parse(raw, model):
    """Return the single JSON text block. Thinking blocks are allowed and ignored."""
    if raw.get("stop_reason") != "end_turn":
        raise AnalysisError("incomplete_response", f"응답이 완료되지 않았습니다: {raw.get('stop_reason')}")
    if raw.get("model") != model:
        raise AnalysisError("model_unavailable", "요청한 모델과 응답 모델이 다릅니다.")
    content = raw.get("content")
    if not isinstance(content, list) or any(
        not isinstance(b, dict) or b.get("type") not in ("text", "thinking", "redacted_thinking") for b in content
    ):
        raise AnalysisError("structured_output_invalid", "응답 블록 형식이 올바르지 않습니다.")
    texts = [b["text"] for b in content if b["type"] == "text"]
    if len(texts) != 1:
        raise AnalysisError("structured_output_invalid", "텍스트 블록이 하나가 아닙니다.")
    text = texts[0].strip()
    if not text:
        raise AnalysisError("empty_response", "모델의 응답이 비어 있습니다.")
    if text.startswith("```"):  # unconstrained replies may wrap the JSON in a code fence
        text = text[3:].removeprefix("json").removesuffix("```").strip()
    try:
        return json.loads(text)
    except ValueError:
        raise AnalysisError("structured_output_invalid", "응답이 JSON이 아닙니다.") from None


def read_stream(response):
    """Rebuild a Messages API response from its server-sent events (stream=true)."""
    raw = {"content": [], "usage": {}}
    event = None
    for line in response:
        line = line.decode().strip()
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        data = json.loads(line[5:])
        if event == "message_start":
            message = data["message"]
            raw.update(model=message.get("model"), usage=dict(message.get("usage") or {}))
        elif event == "content_block_start":
            raw["content"].append(dict(data["content_block"]))
        elif event == "content_block_delta" and data["delta"].get("type") == "text_delta":
            block = raw["content"][data["index"]]
            block["text"] = block.get("text", "") + data["delta"]["text"]
        elif event == "message_delta":
            raw["stop_reason"] = data["delta"].get("stop_reason")
            raw["usage"].update(data.get("usage") or {})
        elif event == "error":
            raise StreamError(data.get("error", {}).get("message", "")[:500])
    return raw


class StreamError(Exception):
    """The server reported an error after the response had started."""


def request_bytes(system, payload, schema):
    """UTF-8 size of what `complete` sends as content: a pre-call estimate, counting one token per byte.
    Generous in practice, but the API does not promise it, and the provider may add its own text."""
    return len((system + json.dumps(schema) + json.dumps(payload, ensure_ascii=False)).encode())


def complete(model, system, payload, schema, log, max_tokens=32000, effort=None, timeout=300, constrained=True):
    """One structured call. Appends a usage/cost record to `log` before parsing, so failed
    outputs are still billed in the run record."""
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise AnalysisError("missing_api_key", "ANTHROPIC_API_KEY가 설정되지 않았습니다.", 503)
    # constrained=False: for schemas too large for the constrained-decoding grammar, the schema
    # goes into the prompt and the caller validates the JSON reply against the same contract.
    output_config = {"format": {"type": "json_schema", "schema": schema}} if constrained else {}
    if not constrained:
        system += "\n\nReply with one JSON object only, matching this JSON schema exactly:\n" + json.dumps(schema)
    if effort:
        output_config["effort"] = effort
    # Streamed so long Opus replies never wait on one idle socket; `timeout` bounds the gap between events.
    body = {"model": model, "max_tokens": max_tokens, "system": system, "stream": True,
            "messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            **({"output_config": output_config} if output_config else {})}
    request = Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode(),
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                               "Content-Type": "application/json"})
    started = time.perf_counter()
    opened = False
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            opened = True
            raw = read_stream(response)
    except HTTPError as exc:
        try:
            detail = json.load(exc).get("error", {}).get("message", "")[:500]
        except ValueError:
            detail = ""
        code, status = {401: ("authentication_failed", 502), 403: ("model_unavailable", 502),
                        404: ("model_unavailable", 502), 429: ("rate_limit", 429)}.get(exc.code, ("gateway_error", 502))
        raise AnalysisError(code, f"Claude API 오류 HTTP {exc.code}: {detail}", status) from None
    except (URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, StreamError) as exc:
        if opened or isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
            # The request reached the API and may be billed; an unknown cost must stop later paid calls.
            log.append({"model": model, "usage": {}, "stop_reason": None, "cost_usd": None,
                        "error": f"interrupted: {type(exc).__name__}",
                        "latency_s": round(time.perf_counter() - started, 2)})
            raise AnalysisError("gateway_error", "Claude API 응답이 중간에 끊겼습니다. 비용 미확인으로 기록했습니다.") from None
        raise AnalysisError("gateway_error", "Claude API에 연결하지 못했습니다.") from None
    usage = raw.get("usage") or {}
    record = {"model": raw.get("model"), "usage": usage, "stop_reason": raw.get("stop_reason"),
              "latency_s": round(time.perf_counter() - started, 2), "cost_usd": None}
    log.append(record)
    if isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int):
        record["cost_usd"] = cost(model, usage)
    return parse(raw, model)


class ClaudeProvider:
    """M1 requirement-loop Provider interface (model_for, complete) on the Claude transport.
    Every stage uses the Sonnet model from SONNET_MODEL; failures raise and never fall back to Mock/SnowChat."""
    mode = "claude"

    def __init__(self):
        if not os.getenv("ANTHROPIC_API_KEY", "").strip():
            raise AnalysisError("missing_api_key", "ANTHROPIC_API_KEY가 설정되지 않았습니다.", 503)
        self.model = model_for("sonnet")

    def model_for(self, stage):
        return self.model

    def complete(self, stage, prompt, payload, output_type, model):
        from app.m2.contracts import output_schema  # schema with inlined $refs, shared with M2 stages
        log = []
        try:
            data = complete(model, prompt, payload, output_schema(output_type), log)
        except AnalysisError as exc:
            exc.usage = usage_of(log)  # a billed call that failed validation still reports its cost
            raise
        return json.dumps(data, ensure_ascii=False), usage_of(log)


def usage_of(log):
    if not log:
        return None
    record = log[-1]
    return {**record["usage"], "cost_usd": record.get("cost_usd"), "model": record.get("model")}
