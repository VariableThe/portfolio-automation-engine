from __future__ import annotations

from datetime import time
from typing import Any

from portfolio_automation.rules.conditions.registry import register_condition
from portfolio_automation.rules.context import RuleContext
from portfolio_automation.rules.models import DecisionResult


def _parse_time_range(value: str) -> tuple[time, time]:
    parts = value.split("-")
    start = time(*map(int, parts[0].split(":")))
    end = time(*map(int, parts[1].split(":")))
    return start, end


@register_condition("market")
def evaluate_market(condition: dict[str, Any], context: RuleContext) -> DecisionResult:
    return DecisionResult(met=True, reason="Market condition always passes")


@register_condition("time_based")
def evaluate_time_based(
    condition: dict[str, Any], context: RuleContext
) -> DecisionResult:
    now = context.current_time
    if now is None:
        return DecisionResult(met=True, reason="No time context, defaulting to true")

    value = condition.get("value")
    if not value or not isinstance(value, str) or "-" not in value:
        return DecisionResult(met=True, reason="No time window configured")

    try:
        start, end = _parse_time_range(value)
    except (ValueError, IndexError):
        return DecisionResult(met=True, reason=f"Invalid time format: {value}")

    current = now.time()
    if start <= current <= end:
        return DecisionResult(
            met=True,
            reason=f"Current time {current.strftime('%H:%M')} within window {value}",
        )
    return DecisionResult(
        met=False,
        reason=f"Current time {current.strftime('%H:%M')} outside window {value}",
    )
