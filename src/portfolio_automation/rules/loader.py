from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

import portfolio_automation.rules.conditions  # noqa: F401
from portfolio_automation.rules.conditions import list_conditions
from portfolio_automation.rules.models import ConditionConfig, RulesConfig


class RuleLoadError(ValueError):
    """Raised when rule configuration is invalid."""


def _validate_condition_type(condition: dict[str, Any], source: str) -> None:
    known = list_conditions()
    cond_type = condition.get("type", "")
    if cond_type not in known:
        msg = (
            f"Unknown condition type '{cond_type}' in {source}. "
            f"Known types: {known}"
        )
        raise RuleLoadError(msg)


def _validate_rule_data(data: dict[str, Any], source: str) -> None:
    for rule in data.get("sell_rules", []):
        cond = rule.get("condition", {})
        _validate_condition_type(cond, f"sell rule for {rule.get('symbol', '?')}")
        ConditionConfig(**cond)

    for entry in data.get("buy_queue", []):
        cond = entry.get("condition", {})
        _validate_condition_type(
            cond, f"buy queue entry for {entry.get('symbol', '?')}"
        )
        ConditionConfig(**cond)


def load_rules(path: str | Path) -> RulesConfig:
    path = Path(path)
    if not path.exists():
        raise RuleLoadError(f"Rules file not found: {path}")

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    _validate_rule_data(data, str(path))
    return RulesConfig(**data)


def load_rules_from_string(content: str, source: str = "<string>") -> RulesConfig:
    data = yaml.safe_load(content) or {}
    _validate_rule_data(data, source)
    return RulesConfig(**data)


def save_rules_from_string(
    content: str, path: str | Path = "config/rules.yaml"
) -> RulesConfig:
    rules = load_rules_from_string(content, source="settings")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return rules
