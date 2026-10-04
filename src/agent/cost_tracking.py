"""LLM cost tracking for Barq executions.

Captures token usage from every LangChain LLM call and accumulates
totals per execution_id. Call `get_accumulator(eid)` to get callbacks
to pass to the LLM, and `pop_accumulator(eid)` at the end to read totals.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Any

from langchain_core.callbacks.base import BaseCallbackHandler

logger = logging.getLogger(__name__)

# Pricing for gemini-3.8-flash via Sprints LiteLLM proxy (USD per 1M tokens)
_PRICE_INPUT_PER_M = 0.075   # $0.075 / 1M input tokens
_PRICE_OUTPUT_PER_M = 0.30   # $0.30  / 1M output tokens


@dataclass
class CostAccumulator:
    tokens_in: int = 0
    tokens_out: int = 0

    def add(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.tokens_in += prompt_tokens
        self.tokens_out += completion_tokens

    @property
    def cost_usd(self) -> float:
        return (
            self.tokens_in  * _PRICE_INPUT_PER_M
            + self.tokens_out * _PRICE_OUTPUT_PER_M
        ) / 1_000_000


def calculate_cost(tokens_in: int, tokens_out: int) -> tuple[float, float, float]:
    """Return (input_cost_usd, output_cost_usd, total_cost_usd)."""
    in_cost = (tokens_in * _PRICE_INPUT_PER_M) / 1_000_000
    out_cost = (tokens_out * _PRICE_OUTPUT_PER_M) / 1_000_000
    return in_cost, out_cost, in_cost + out_cost


def snapshot_cost(execution_id: str | None) -> dict[str, Any]:
    """Token/cost totals for DB persistence and Langfuse, or {} if nothing recorded."""
    if not execution_id:
        return {}
    acc = get_accumulator(str(execution_id))
    if not acc.tokens_in and not acc.tokens_out:
        return {}
    _in, _out, total = calculate_cost(acc.tokens_in, acc.tokens_out)
    return {
        "total_tokens_in": acc.tokens_in,
        "total_tokens_out": acc.tokens_out,
        "estimated_cost_usd": acc.cost_usd,
        "input_cost_usd": _in,
        "output_cost_usd": _out,
        "total_cost_usd": total,
    }


def _as_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _tokens_from_mapping(data: Any) -> tuple[int, int]:
    if data is None:
        return 0, 0
    if not isinstance(data, dict):
        data = {
            "input_tokens": getattr(data, "input_tokens", None),
            "output_tokens": getattr(data, "output_tokens", None),
            "prompt_tokens": getattr(data, "prompt_tokens", None),
            "completion_tokens": getattr(data, "completion_tokens", None),
        }
    prompt = _as_int(
        data.get("prompt_tokens")
        or data.get("input_tokens")
        or data.get("promptTokens")
        or data.get("input")
    )
    completion = _as_int(
        data.get("completion_tokens")
        or data.get("output_tokens")
        or data.get("completionTokens")
        or data.get("output")
    )
    return prompt, completion


def extract_token_usage(response: Any) -> tuple[int, int]:
    """Read prompt/completion tokens from a LangChain LLMResult / chat result.

    ChatOpenAI (Gemini via LiteLLM) often stores usage on the AIMessage
    (``usage_metadata`` / ``response_metadata``) rather than
    ``llm_output.token_usage``.
    """
    llm_output = getattr(response, "llm_output", None) or {}
    if isinstance(llm_output, dict):
        prompt, completion = _tokens_from_mapping(
            llm_output.get("token_usage") or llm_output.get("usage")
        )
        if prompt or completion:
            return prompt, completion

    generations = getattr(response, "generations", None) or []
    prompt = completion = 0
    for gen_list in generations:
        rows = gen_list if isinstance(gen_list, (list, tuple)) else [gen_list]
        for gen in rows:
            if gen is None:
                continue
            message = getattr(gen, "message", None)
            for blob in (
                getattr(message, "usage_metadata", None) if message is not None else None,
                (getattr(message, "response_metadata", None) or {}).get("token_usage")
                if message is not None and isinstance(getattr(message, "response_metadata", None), dict)
                else None,
                (getattr(message, "response_metadata", None) or {}).get("usage")
                if message is not None and isinstance(getattr(message, "response_metadata", None), dict)
                else None,
                getattr(gen, "generation_info", None),
            ):
                p, c = _tokens_from_mapping(blob)
                if p or c:
                    prompt += p
                    completion += c
                    break
    return prompt, completion


class _CostCallback(BaseCallbackHandler):
    """LangChain callback that feeds token counts into a CostAccumulator."""

    def __init__(self, accumulator: CostAccumulator) -> None:
        super().__init__()
        self._acc = accumulator

    def _ingest(self, response: Any) -> None:
        try:
            prompt, completion = extract_token_usage(response)
            if prompt or completion:
                self._acc.add(prompt, completion)
                logger.debug(
                    "LLM usage captured: in=%d out=%d cumulative_cost=$%.6f",
                    prompt, completion, self._acc.cost_usd,
                )
            else:
                logger.debug("LLM usage missing on response; cost not updated")
        except Exception:
            logger.debug("LLM usage ingest failed", exc_info=True)

    def on_llm_end(self, response, **kwargs) -> None:  # type: ignore[override]
        self._ingest(response)

    def on_chat_model_end(self, response, **kwargs) -> None:  # type: ignore[override]
        # ChatOpenAI (Gemini) fires this instead of on_llm_end.
        self._ingest(response)


# Per-execution registry (worker-process-local; safe for Celery prefork)
_registry: dict[str, CostAccumulator] = {}
_lock = Lock()


def get_accumulator(execution_id: str) -> CostAccumulator:
    """Return (or lazily create) the accumulator for this execution."""
    with _lock:
        if execution_id not in _registry:
            _registry[execution_id] = CostAccumulator()
        return _registry[execution_id]


def pop_accumulator(execution_id: str) -> CostAccumulator | None:
    """Remove and return the accumulator; returns None if not found."""
    with _lock:
        return _registry.pop(execution_id, None)


def consume_cost_fields(execution_id: str | None) -> dict[str, int | float]:
    """Snapshot + drop totals for persisting onto an execution row."""
    if not execution_id:
        return {}
    snap = snapshot_cost(str(execution_id))
    pop_accumulator(str(execution_id))
    if not snap:
        return {}
    return {
        "total_tokens_in": snap["total_tokens_in"],
        "total_tokens_out": snap["total_tokens_out"],
        "estimated_cost_usd": snap["estimated_cost_usd"],
    }


def get_cost_callback(execution_id: str) -> list[_CostCallback]:
    """Return a LangChain callback list for the given execution."""
    acc = get_accumulator(execution_id)
    return [_CostCallback(acc)]
