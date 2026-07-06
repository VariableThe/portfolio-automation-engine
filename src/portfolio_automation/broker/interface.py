from __future__ import annotations

from typing import Protocol, runtime_checkable

from portfolio_automation.broker.models import Balance, Holding, Order, Position


@runtime_checkable
class Broker(Protocol):
    async def authenticate(self) -> None: ...

    async def get_holdings(self) -> list[Holding]: ...

    async def get_positions(self) -> list[Position]: ...

    async def get_balance(self) -> Balance: ...

    async def get_orders(self) -> list[Order]: ...

    async def get_price(self, symbol: str) -> float: ...

    async def place_buy_order(
        self,
        symbol: str,
        quantity: int,
        order_type: str,
        price: float | None = None,
    ) -> Order: ...

    async def place_sell_order(
        self,
        symbol: str,
        quantity: int,
        order_type: str,
        price: float | None = None,
    ) -> Order: ...

    async def cancel_order(self, order_id: str) -> None: ...

    async def get_order_status(self, order_id: str) -> Order: ...
