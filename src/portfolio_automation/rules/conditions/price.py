from __future__ import annotations

from typing import Any

from portfolio_automation.rules.conditions.registry import register_condition
from portfolio_automation.rules.context import RuleContext
from portfolio_automation.rules.models import DecisionResult


@register_condition("price_above")
def evaluate_price_above(
    condition: dict[str, Any], context: RuleContext
) -> DecisionResult:
    target = float(condition["value"])
    if context.current_price > target:
        return DecisionResult(
            met=True,
            reason=(
                f"{context.symbol} price ₹{context.current_price:.2f} "
                f"> threshold ₹{target:.2f}"
            ),
        )
    return DecisionResult(
        met=False,
        reason=(
            f"{context.symbol} price ₹{context.current_price:.2f} "
            f"≤ threshold ₹{target:.2f}"
        ),
    )


@register_condition("price_below")
def evaluate_price_below(
    condition: dict[str, Any], context: RuleContext
) -> DecisionResult:
    target = float(condition["value"])
    if context.current_price < target:
        return DecisionResult(
            met=True,
            reason=(
                f"{context.symbol} price ₹{context.current_price:.2f} "
                f"< threshold ₹{target:.2f}"
            ),
        )
    return DecisionResult(
        met=False,
        reason=(
            f"{context.symbol} price ₹{context.current_price:.2f} "
            f"≥ threshold ₹{target:.2f}"
        ),
    )
