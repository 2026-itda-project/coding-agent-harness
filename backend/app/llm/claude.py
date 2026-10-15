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
    if text.startswith("```"):  # unconstrained replies may wrap the JSON in a code fence
        text = text[3:].removeprefix("json").removesuffix("```").strip()
    try:
        return json.loads(text)
    except ValueError:
        raise AnalysisError("structured_output_invalid", "응답이 JSON이 아닙니다.") from None


def complete(model, system, payload, schema, log, max_tokens=32000, effort=None, timeout=900, constrained=True):
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
    body = {"model": model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            **({"output_config": output_config} if output_config else {})}
    request = Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode(),
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                               "Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = json.load(response)
    except HTTPError as exc:
        try:
            detail = json.load(exc).get("error", {}).get("message", "")[:500]
        except ValueError:
            detail = ""
        raise AnalysisError("gateway_error", f"Claude API 오류 HTTP {exc.code}: {detail}") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise AnalysisError("gateway_error", "Claude API에 연결하지 못했습니다.") from None
    usage = raw.get("usage") or {}
    record = {"model": raw.get("model"), "usage": usage, "stop_reason": raw.get("stop_reason"),
              "latency_s": round(time.perf_counter() - started, 2)}
    if isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int):
        record["cost_usd"] = cost(model, usage)
    log.append(record)
    return parse(raw, model)
