from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Holding(BaseModel):
    symbol: str
    quantity: int
    avg_price: float
    current_price: float | None = None
    pnl: float | None = None


class Position(BaseModel):
    symbol: str
    quantity: int
    avg_price: float
    current_price: float | None = None
    pnl: float | None = None
    day_buy_quantity: int = 0
    day_sell_quantity: int = 0


class Order(BaseModel):
    id: str
    symbol: str
    side: str
    order_type: str
    quantity: int
    price: float | None = None
    status: str = "PENDING"
    filled_quantity: int = 0
    filled_price: float | None = None
    placed_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


class OrderFill(BaseModel):
    order_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    executed_at: datetime | None = None


class Balance(BaseModel):
    available_cash: float
    used_cash: float = 0.0
    total_cash: float = 0.0
