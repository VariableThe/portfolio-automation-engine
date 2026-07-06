from __future__ import annotations

from collections.abc import Callable
from typing import Any

from portfolio_automation.rules.context import RuleContext
from portfolio_automation.rules.models import DecisionResult

ConditionEvaluator = Callable[
    [dict[str, Any], RuleContext],
    DecisionResult,
]

_registry: dict[str, ConditionEvaluator] = {}


def register_condition(name: str) -> Callable[[ConditionEvaluator], ConditionEvaluator]:
    def decorator(func: ConditionEvaluator) -> ConditionEvaluator:
        _registry[name] = func
        return func

    return decorator


def get_condition(name: str) -> ConditionEvaluator:
    if name not in _registry:
        msg = f"Unknown condition type: '{name}'. Known types: {list(_registry)}"
        raise UnknownConditionError(msg)
    return _registry[name]


def list_conditions() -> list[str]:
    return list(_registry)


class UnknownConditionError(LookupError):
    """Raised when a condition type is not in the registry."""
