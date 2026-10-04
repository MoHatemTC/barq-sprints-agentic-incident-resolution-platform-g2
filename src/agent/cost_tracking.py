"""LLM cost tracking for Barq executions.

Captures token usage from every LangChain LLM call and accumulates
totals per execution_id. Call `get_accumulator(eid)` to get callbacks
to pass to the LLM, and `pop_accumulator(eid)` at the end to read totals.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import Lock

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


class _CostCallback(BaseCallbackHandler):
    """LangChain callback that feeds token counts into a CostAccumulator."""

    def __init__(self, accumulator: CostAccumulator) -> None:
        super().__init__()
        self._acc = accumulator

    def on_llm_end(self, response, **kwargs) -> None:  # type: ignore[override]
        try:
            usage = (response.llm_output or {}).get("token_usage", {})
            prompt = int(usage.get("prompt_tokens", 0))
            completion = int(usage.get("completion_tokens", 0))
            if prompt or completion:
                self._acc.add(prompt, completion)
                logger.debug(
                    "LLM usage captured: in=%d out=%d cumulative_cost=$%.6f",
                    prompt, completion, self._acc.cost_usd,
                )
        except Exception:
            pass  # never break the caller


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


def get_cost_callback(execution_id: str) -> list[_CostCallback]:
    """Return a LangChain callback list for the given execution."""
    acc = get_accumulator(execution_id)
    return [_CostCallback(acc)]
