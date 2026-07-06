from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DecisionResult(BaseModel):
    met: bool
    reason: str
    details: dict[str, Any] | None = None


class ConditionConfig(BaseModel):
    type: str
    value: float | str | None = None
    expression: str | None = None
    period: int | None = None
    fast_period: int | None = None
    slow_period: int | None = None
    signal_period: int | None = None
    upper: float | None = None
    lower: float | None = None


class SellRule(BaseModel):
    symbol: str
    condition: ConditionConfig


class BuyQueueEntry(BaseModel):
    symbol: str
    priority: int = Field(default=1, ge=1)
    condition: ConditionConfig


class RulesConfig(BaseModel):
    sell_rules: list[SellRule] = Field(default_factory=list)
    buy_queue: list[BuyQueueEntry] = Field(default_factory=list)
