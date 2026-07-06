from __future__ import annotations

from typing import Any

from portfolio_automation.rules.conditions.registry import register_condition
from portfolio_automation.rules.context import RuleContext
from portfolio_automation.rules.models import DecisionResult


@register_condition("allocation_percentage")
def evaluate_allocation(
    condition: dict[str, Any], context: RuleContext
) -> DecisionResult:
    max_pct = float(condition.get("value", 0.2))

    current_value = context.current_price * context.quantity
    buy_cost = context.current_price
    total_value = context.portfolio_value

    if total_value <= 0:
        return DecisionResult(
            met=True,
            reason=f"No portfolio value, can allocate up to {max_pct:.1%}",
        )

    new_allocation = (current_value + buy_cost) / total_value
    if new_allocation <= max_pct:
        return DecisionResult(
            met=True,
            reason=(
                f"{context.symbol} allocation {new_allocation:.1%} "
                f"≤ max {max_pct:.1%} (current ₹{current_value:.0f} + buy ₹{buy_cost:.0f})"
            ),
        )
    return DecisionResult(
        met=False,
        reason=(
            f"{context.symbol} allocation {new_allocation:.1%} "
            f"> max {max_pct:.1%} (current ₹{current_value:.0f} + buy ₹{buy_cost:.0f})"
        ),
    )
