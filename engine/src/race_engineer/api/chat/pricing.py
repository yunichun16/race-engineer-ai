"""Token counts and what they cost, for the per-question log line and the `done` event.

Prices are US dollars per million tokens on the Claude API. Cache writes are priced at the
5-minute rate, the only TTL the chat uses. M7 builds its daily spending cap on these numbers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Price:
    """Dollars per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float  # 5-minute TTL


SONNET = Price(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50)
# The chat's model, and Claude Sonnet 5, which serves a question when the server-side fallback
# takes over from a declined one.
PRICES: dict[str, Price] = {"claude-sonnet-5-5": SONNET, "claude-sonnet-5": SONNET}


@dataclass
class Usage:
    """Tokens and dollars summed over a question's API calls."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0

    def add(self, usage: Any, model: str) -> None:
        """Add one response's `usage` (a BetaUsage), priced for `model`.

        After a server-side fallback the top-level counts cover only the attempt that answered,
        so the per-attempt `iterations` are summed instead, each at its own model's price.
        """
        attempts = [
            it
            for it in (getattr(usage, "iterations", None) or [])
            if getattr(it, "type", None) in ("message", "fallback_message")
        ]
        for attempt in attempts or [usage]:
            counts = (
                attempt.input_tokens or 0,
                attempt.output_tokens or 0,
                attempt.cache_read_input_tokens or 0,
                attempt.cache_creation_input_tokens or 0,
            )
            self.input += counts[0]
            self.output += counts[1]
            self.cache_read += counts[2]
            self.cache_write += counts[3]
            self.cost_usd += cost_usd(*counts, model=getattr(attempt, "model", None) or model)

    def as_dict(self) -> dict[str, int]:
        return {
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
        }


def price_of(model: str) -> Price:
    """The model's prices; an unknown model (the scripted stand-in) is priced as Sonnet."""
    price = PRICES.get(model)
    if price is None:
        if model != "scripted":
            log.warning("no price for model %s; using Claude Sonnet 5.5 prices", model)
        return SONNET
    return price


def cost_usd(
    input_tokens: int, output_tokens: int, cache_read: int, cache_write: int, *, model: str
) -> float:
    """Dollars for one response's tokens."""
    price = price_of(model)
    return (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read * price.cache_read
        + cache_write * price.cache_write
    ) / 1_000_000
