from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class RuleContext:
    symbol: str
    current_price: float
    avg_buy_price: float | None = None
    quantity: int = 0
    portfolio_value: float = 0.0
    available_cash: float = 0.0
    historical_candles: list[dict[str, Any]] | None = None
    current_time: datetime | None = None
