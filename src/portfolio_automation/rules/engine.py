from __future__ import annotations

from typing import Any

from portfolio_automation.rules.conditions import get_condition
from portfolio_automation.rules.context import RuleContext
from portfolio_automation.rules.models import BuyQueueEntry as BuyQueueModel
from portfolio_automation.rules.models import DecisionResult, RulesConfig


class RuleEngine:
    def __init__(self, rules_config: RulesConfig) -> None:
        self._rules = rules_config

    def evaluate_sell_rules(self, context: RuleContext) -> list[DecisionResult]:
        results: list[DecisionResult] = []
        for rule in self._rules.sell_rules:
            if rule.symbol != context.symbol:
                continue
            result = self._evaluate_single(rule.condition.model_dump(), context)
            results.append(result)
        return results

    def evaluate_buy_condition(
        self, entry: BuyQueueModel, context: RuleContext
    ) -> DecisionResult:
        return self._evaluate_single(entry.condition.model_dump(), context)

    def evaluate_raw(
        self, condition: dict[str, Any], context: RuleContext
    ) -> DecisionResult:
        return self._evaluate_single(condition, context)

    @staticmethod
    def _evaluate_single(
        condition: dict[str, Any], context: RuleContext
    ) -> DecisionResult:
        evaluator = get_condition(condition["type"])
        return evaluator(condition, context)
