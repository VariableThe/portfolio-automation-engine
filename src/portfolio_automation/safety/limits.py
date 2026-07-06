from __future__ import annotations

import logging

from portfolio_automation.persistence.repository import Repository

logger = logging.getLogger(__name__)


class DailyLimits:
    def __init__(
        self,
        repository: Repository,
        max_daily_trades: int = 10,
        max_daily_investment: float = 100_000.0,
        max_allocation_per_stock: float = 0.20,
    ) -> None:
        self._repo = repository
        self._max_daily_trades = max_daily_trades
        self._max_daily_investment = max_daily_investment
        self._max_allocation_per_stock = max_allocation_per_stock

    async def check_trade_count(self) -> str | None:
        count = await self._repo.get_daily_trade_count()
        if count >= self._max_daily_trades:
            return f"Daily trade limit reached: {count}/{self._max_daily_trades}"
        return None

    async def check_buy_investment(self, additional_cost: float) -> str | None:
        total = await self._repo.get_daily_buy_investment()
        if total + additional_cost > self._max_daily_investment:
            return (
                f"Daily investment limit would be exceeded: "
                f"{total + additional_cost:.2f} > {self._max_daily_investment:.2f}"
            )
        return None

    async def check_allocation(
        self, symbol: str, additional_value: float, portfolio_value: float
    ) -> str | None:
        if portfolio_value <= 0:
            return None

        current_holding_value = 0.0
        holdings = await self._repo.get_latest_holdings()
        for h in holdings:
            if h.symbol == symbol and h.current_price:
                current_holding_value = h.quantity * h.current_price

        total_value = current_holding_value + additional_value
        allocation = total_value / portfolio_value
        if allocation > self._max_allocation_per_stock:
            return (
                f"Allocation limit for {symbol}: "
                f"{allocation:.1%} > {self._max_allocation_per_stock:.1%}"
            )
        return None
