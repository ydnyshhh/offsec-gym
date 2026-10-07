"""Frozen no-retry provider-health decisions for confirmatory collection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TRANSIENT_PROVIDER_REASONS = frozenset(
    {"provider_unavailable", "provider_timeout", "provider_server_error", "provider_rate_limited"}
)
TRANSPORT_REASONS = frozenset({"provider_unavailable", "provider_timeout", "provider_server_error"})


@dataclass(frozen=True)
class CellTerminal:
    status: str
    failure_reason: str | None = None


def provider_health(history: tuple[CellTerminal, ...]) -> Literal["continue", "pause", "stop"]:
    """Decide after an audited cell; never infer permission to retry it."""
    if not history:
        return "continue"
    current = history[-1]
    if current.status != "provider_failed":
        return "continue"
    if current.failure_reason not in TRANSIENT_PROVIDER_REASONS:
        return "stop"
    if len(history) >= 2 and all(
        item.status == "provider_failed" and item.failure_reason in TRANSPORT_REASONS
        for item in history[-2:]
    ):
        return "pause"
    if sum(item.status == "provider_failed" for item in history[-10:]) >= 3:
        return "pause"
    return "continue"
