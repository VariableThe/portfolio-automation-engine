from __future__ import annotations

from typing import Any

from portfolio_automation.rules.conditions.registry import register_condition
from portfolio_automation.rules.context import RuleContext
from portfolio_automation.rules.models import DecisionResult


def _calc_pct(current: float, buy: float) -> float:
    if buy == 0:
        return 0.0
    return ((current - buy) / buy) * 100


@register_condition("percent_profit")
def evaluate_percent_profit(
    condition: dict[str, Any], context: RuleContext
) -> DecisionResult:
    target_pct = float(condition["value"])
    if context.avg_buy_price is None or context.avg_buy_price == 0:
        return DecisionResult(
            met=False,
            reason=f"No buy price available for {context.symbol}",
        )
    pct = _calc_pct(context.current_price, context.avg_buy_price)
    if pct >= target_pct:
        return DecisionResult(
            met=True,
            reason=(
                f"{context.symbol} gain {pct:.2f}% "
                f"≥ target {target_pct:.2f}% "
                f"(buy ₹{context.avg_buy_price:.2f} → now ₹{context.current_price:.2f})"
            ),
        )
    return DecisionResult(
        met=False,
        reason=(f"{context.symbol} gain {pct:.2f}% " f"< target {target_pct:.2f}%"),
    )


@register_condition("percent_loss")
def evaluate_percent_loss(
    condition: dict[str, Any], context: RuleContext
) -> DecisionResult:
    target_pct = abs(float(condition["value"]))
    if context.avg_buy_price is None or context.avg_buy_price == 0:
        return DecisionResult(
            met=False,
            reason=f"No buy price available for {context.symbol}",
        )
    pct = _calc_pct(context.current_price, context.avg_buy_price)
    if pct <= -target_pct:
        return DecisionResult(
            met=True,
            reason=(
                f"{context.symbol} loss {pct:.2f}% "
                f"≤ -{target_pct:.2f}% stop-loss triggered "
                f"(buy ₹{context.avg_buy_price:.2f} → now ₹{context.current_price:.2f})"
            ),
        )
    return DecisionResult(
        met=False,
        reason=(
            f"{context.symbol} change {pct:.2f}% "
            f"> -{target_pct:.2f}% stop-loss not triggered"
        ),
    )
