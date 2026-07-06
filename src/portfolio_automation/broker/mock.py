from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime

from portfolio_automation.broker.models import (
    Balance,
    Holding,
    Order,
    OrderFill,
    Position,
)
from portfolio_automation.core.exceptions import (
    BrokerAuthenticationError,
    BrokerOrderRejectedError,
    BrokerSymbolNotFoundError,
)


class _MockOrderState:
    def __init__(
        self,
        order: Order,
        fill_delay: float,
        reject_on_price_deviation: float | None = None,
    ) -> None:
        self.order = order
        self.fill_delay = fill_delay
        self.reject_on_price_deviation = reject_on_price_deviation
        self.created_at = datetime.now(UTC)
        self.fills: list[OrderFill] = []
        self.next_fill_index = 0

    @property
    def is_fully_filled(self) -> bool:
        return self.order.filled_quantity >= self.order.quantity

    @property
    def is_terminal(self) -> bool:
        return self.order.status in ("COMPLETED", "REJECTED", "CANCELLED")


class MockBroker:
    def __init__(
        self,
        initial_cash: float = 100_000.0,
        latency: float = 0.05,
        fill_delay: float = 0.1,
        partial_fill_enabled: bool = True,
        reject_probability: float = 0.0,
    ) -> None:
        self._cash = initial_cash
        self._latency = latency
        self._fill_delay = fill_delay
        self._partial_fill_enabled = partial_fill_enabled
        self._reject_probability = reject_probability
        self._authenticated = False
        self._order_counter = 0

        self._holdings: dict[str, dict] = {
            "INFY": {"quantity": 10, "avg_price": 1500.0},
            "TCS": {"quantity": 5, "avg_price": 3500.0},
        }

        self._base_prices: dict[str, float] = {
            "INFY": 1550.0,
            "TCS": 3600.0,
            "HAL": 4800.0,
            "BEL": 500.0,
            "RELIANCE": 2500.0,
            "WIPRO": 450.0,
            "HDFC": 1600.0,
        }

        self._orders: dict[str, _MockOrderState] = {}
        self._fill_tasks: set[asyncio.Task] = set()

    async def _simulate_latency(self) -> None:
        if self._latency > 0:
            await asyncio.sleep(self._latency * random.uniform(0.5, 1.5))

    def _require_auth(self) -> None:
        if not self._authenticated:
            raise BrokerAuthenticationError(
                "Not authenticated. Call authenticate() first."
            )

    def _get_current_price(self, symbol: str) -> float:
        base = self._base_prices.get(symbol)
        if base is None:
            raise BrokerSymbolNotFoundError(
                f"Symbol '{symbol}' not found in mock data."
            )
        fluctuation = base * random.uniform(-0.01, 0.01)
        return round(base + fluctuation, 2)

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"MOCK{datetime.now(UTC).strftime('%y%m%d')}{self._order_counter:06d}"

    def _update_holdings_buy(
        self, order: Order, fill_price: float, fill_qty: int
    ) -> None:
        if order.symbol not in self._holdings:
            self._holdings[order.symbol] = {"quantity": 0, "avg_price": 0.0}
        h = self._holdings[order.symbol]
        total_cost = h["avg_price"] * h["quantity"] + fill_price * fill_qty
        h["quantity"] += fill_qty
        h["avg_price"] = (
            round(total_cost / h["quantity"], 2) if h["quantity"] > 0 else 0.0
        )

    def _update_holdings_sell(
        self, order: Order, fill_price: float, fill_qty: int
    ) -> None:
        h = self._holdings.get(order.symbol)
        if h is None or h["quantity"] < fill_qty:
            raise BrokerOrderRejectedError(
                f"Insufficient {order.symbol} shares: have "
                f"{h['quantity'] if h else 0}, need {fill_qty}"
            )
        h["quantity"] -= fill_qty
        if h["quantity"] == 0:
            del self._holdings[order.symbol]

    async def _fill_order(self, order_state: _MockOrderState) -> None:
        await asyncio.sleep(order_state.fill_delay)

        if order_state.is_terminal:
            return

        order = order_state.order
        remaining = order.quantity - order.filled_quantity

        if self._partial_fill_enabled and remaining > 1:
            first_chunk = max(1, remaining // 2)
            second_chunk = remaining - first_chunk

            for chunk in (first_chunk, second_chunk):
                if order_state.is_terminal:
                    break

                await asyncio.sleep(order_state.fill_delay)
                price = self._get_current_price(order.symbol)

                fill = OrderFill(
                    order_id=order.id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=chunk,
                    price=price,
                    executed_at=datetime.now(UTC),
                )
                order_state.fills.append(fill)
                order.filled_quantity += chunk
                order.filled_price = price
                order.status = (
                    "PARTIALLY_FILLED"
                    if not order_state.is_fully_filled
                    else "COMPLETED"
                )

                if order.side == "BUY":
                    self._update_holdings_buy(order, price, chunk)
                    self._cash -= chunk * price
                else:
                    self._update_holdings_sell(order, price, chunk)
                    self._cash += chunk * price

        else:
            price = self._get_current_price(order.symbol)
            fill = OrderFill(
                order_id=order.id,
                symbol=order.symbol,
                side=order.side,
                quantity=remaining,
                price=price,
                executed_at=datetime.now(UTC),
            )
            order_state.fills.append(fill)
            order.filled_quantity += remaining
            order.filled_price = price
            order.status = "COMPLETED"

            if order.side == "BUY":
                self._update_holdings_buy(order, price, remaining)
                self._cash -= remaining * price
            else:
                self._update_holdings_sell(order, price, remaining)
                self._cash += remaining * price

        order.completed_at = datetime.now(UTC)

    # ── Broker Protocol Implementation ──────────────────────────────────

    async def authenticate(self) -> None:
        await self._simulate_latency()
        self._authenticated = True

    async def get_holdings(self) -> list[Holding]:
        self._require_auth()
        await self._simulate_latency()
        result: list[Holding] = []
        for symbol, data in self._holdings.items():
            current_price = self._get_current_price(symbol)
            qty = data["quantity"]
            avg = data["avg_price"]
            result.append(
                Holding(
                    symbol=symbol,
                    quantity=qty,
                    avg_price=avg,
                    current_price=current_price,
                    pnl=round((current_price - avg) * qty, 2),
                )
            )
        return result

    async def get_positions(self) -> list[Position]:
        self._require_auth()
        await self._simulate_latency()
        result: list[Position] = []
        for symbol, data in self._holdings.items():
            current_price = self._get_current_price(symbol)
            qty = data["quantity"]
            avg = data["avg_price"]
            result.append(
                Position(
                    symbol=symbol,
                    quantity=qty,
                    avg_price=avg,
                    current_price=current_price,
                    pnl=round((current_price - avg) * qty, 2),
                )
            )
        return result

    async def get_balance(self) -> Balance:
        self._require_auth()
        await self._simulate_latency()
        used = 0.0
        for ostate in self._orders.values():
            if (
                ostate.order.status in ("OPEN", "PARTIALLY_FILLED")
                and ostate.order.side == "BUY"
            ):
                remaining = ostate.order.quantity - ostate.order.filled_quantity
                price = ostate.order.price or self._base_prices.get(
                    ostate.order.symbol, 0
                )
                used += remaining * price
        return Balance(
            available_cash=round(self._cash - used, 2),
            used_cash=round(used, 2),
            total_cash=round(self._cash, 2),
        )

    async def get_orders(self) -> list[Order]:
        self._require_auth()
        await self._simulate_latency()
        return [ostate.order for ostate in self._orders.values()]

    async def get_price(self, symbol: str) -> float:
        self._require_auth()
        await self._simulate_latency()
        if symbol not in self._base_prices:
            raise BrokerSymbolNotFoundError(
                f"Symbol '{symbol}' not found in mock data."
            )
        return self._get_current_price(symbol)

    async def place_buy_order(
        self,
        symbol: str,
        quantity: int,
        order_type: str,
        price: float | None = None,
    ) -> Order:
        self._require_auth()
        await self._simulate_latency()

        if symbol not in self._base_prices:
            raise BrokerSymbolNotFoundError(f"Symbol '{symbol}' not found.")

        if quantity <= 0:
            raise BrokerOrderRejectedError("Quantity must be positive.")

        if random.random() < self._reject_probability:
            raise BrokerOrderRejectedError("Simulated order rejection.")

        current_price = self._get_current_price(symbol)
        estimated_cost = quantity * (price or current_price)

        if estimated_cost > self._cash:
            raise BrokerOrderRejectedError(
                f"Insufficient funds: need ₹{estimated_cost:.2f}, have ₹{self._cash:.2f}"
            )

        order = Order(
            id=self._next_order_id(),
            symbol=symbol,
            side="BUY",
            order_type=order_type,
            quantity=quantity,
            price=price or current_price,
            status="OPEN",
            placed_at=datetime.now(UTC),
        )

        order_state = _MockOrderState(
            order=order,
            fill_delay=self._fill_delay,
        )
        self._orders[order.id] = order_state

        task = asyncio.create_task(self._fill_order(order_state))
        self._fill_tasks.add(task)
        task.add_done_callback(self._fill_tasks.discard)

        return order

    async def place_sell_order(
        self,
        symbol: str,
        quantity: int,
        order_type: str,
        price: float | None = None,
    ) -> Order:
        self._require_auth()
        await self._simulate_latency()

        if symbol not in self._base_prices:
            raise BrokerSymbolNotFoundError(f"Symbol '{symbol}' not found.")

        if quantity <= 0:
            raise BrokerOrderRejectedError("Quantity must be positive.")

        holding = self._holdings.get(symbol)
        if holding is None or holding["quantity"] < quantity:
            raise BrokerOrderRejectedError(
                f"Insufficient {symbol} shares: have "
                f"{holding['quantity'] if holding else 0}, need {quantity}"
            )

        if random.random() < self._reject_probability:
            raise BrokerOrderRejectedError("Simulated order rejection.")

        current_price = self._get_current_price(symbol)

        order = Order(
            id=self._next_order_id(),
            symbol=symbol,
            side="SELL",
            order_type=order_type,
            quantity=quantity,
            price=price or current_price,
            status="OPEN",
            placed_at=datetime.now(UTC),
        )

        order_state = _MockOrderState(
            order=order,
            fill_delay=self._fill_delay,
        )
        self._orders[order.id] = order_state

        task = asyncio.create_task(self._fill_order(order_state))
        self._fill_tasks.add(task)
        task.add_done_callback(self._fill_tasks.discard)

        return order

    async def cancel_order(self, order_id: str) -> None:
        self._require_auth()
        await self._simulate_latency()
        ostate = self._orders.get(order_id)
        if ostate is None:
            raise BrokerOrderRejectedError(f"Order '{order_id}' not found.")
        if ostate.is_terminal:
            raise BrokerOrderRejectedError(
                f"Cannot cancel order in terminal state '{ostate.order.status}'."
            )
        ostate.order.status = "CANCELLED"

    async def get_order_status(self, order_id: str) -> Order:
        self._require_auth()
        await self._simulate_latency()
        ostate = self._orders.get(order_id)
        if ostate is None:
            raise BrokerOrderRejectedError(f"Order '{order_id}' not found.")
        return ostate.order
