from __future__ import annotations

from typing import Any

import simpleeval

from portfolio_automation.core.exceptions import RuleEvaluationError, RuleSecurityError
from portfolio_automation.rules.conditions.registry import register_condition
from portfolio_automation.rules.context import RuleContext
from portfolio_automation.rules.models import DecisionResult

_FORBIDDEN_PATTERNS = [
    "__builtins__",
    "__import__",
    "import ",
    "os.",
    "sys.",
    "open(",
    "eval(",
    "exec(",
    "compile(",
    "getattr",
    "setattr",
    "delattr",
    "vars(",
    "globals(",
    "locals(",
    "type(",
    ".__class__",
    ".__subclasses__",
    ".__base__",
]


def _check_safe(expression: str) -> None:
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern in expression:
            raise RuleSecurityError(
                f"Expression contains forbidden pattern '{pattern}': {expression!r}"
            )


def _build_safe_names(context: RuleContext) -> dict[str, Any]:
    names: dict[str, Any] = {
        "price": context.current_price,
        "avg_price": context.avg_buy_price or 0,
        "quantity": context.quantity,
        "portfolio_value": context.portfolio_value,
        "available_cash": context.available_cash,
    }

    avg = context.avg_buy_price
    if avg and avg > 0:
        names["pct"] = ((context.current_price - avg) / avg) * 100
    else:
        names["pct"] = 0.0

    return names


def _build_safe_functions() -> dict[str, Any]:
    return {
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "int": int,
        "float": float,
        "str": str,
        "len": len,
        "bool": bool,
    }


@register_condition("custom_expression")
def evaluate_custom_expression(
    condition: dict[str, Any], context: RuleContext
) -> DecisionResult:
    expression = condition.get("expression", condition.get("value", ""))
    if not expression or not isinstance(expression, str):
        return DecisionResult(
            met=False,
            reason="No expression provided for custom_expression condition",
        )

    _check_safe(expression)
    names = _build_safe_names(context)
    functions = _build_safe_functions()
    names.update({"True": True, "False": False})

    try:
        result = simpleeval.simple_eval(expression, names=names, functions=functions)
    except Exception as exc:
        raise RuleEvaluationError(
            f"Custom expression evaluation failed: {exc}"
        ) from exc

    met = bool(result)
    return DecisionResult(
        met=met,
        reason=(
            f"Expression '{expression}' evaluated to {met} "
            f"(price={names['price']}, pct={names['pct']:.2f}%)"
        ),
    )
