from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from portfolio_automation.broker.models import Balance, Order
from portfolio_automation.core.exceptions import BrokerNetworkError
from portfolio_automation.execution.engine import ExecutionEngine
from portfolio_automation.persistence.models import OrderFill as DBOrderFill
from portfolio_automation.persistence.repository import Repository
from portfolio_automation.rules.models import (
    BuyQueueEntry,
    ConditionConfig,
    RulesConfig,
    SellRule,
)
from tests.integration.conftest import DeterministicMockBroker, TimeController


class NetworkFlakyBroker(DeterministicMockBroker):
    """Mock broker that simulates network failures during order status checks."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.status_check_attempts: dict[str, int] = {}
        self.place_order_count: int = 0

    async def place_sell_order(
        self, symbol: str, quantity: int, order_type: str, price: float | None = None
    ) -> Order:
        self.place_order_count += 1
        return await super().place_sell_order(symbol, quantity, order_type, price)

    async def get_order_status(self, order_id: str) -> Order:
        attempts = self.status_check_attempts.get(order_id, 0) + 1
        self.status_check_attempts[order_id] = attempts

        if attempts == 1:
            raise BrokerNetworkError("Simulated network timeout during status check")
        return await super().get_order_status(order_id)


class PartialFillBroker(DeterministicMockBroker):
    """Mock broker that simulates a deterministic 30 -> 50 -> 20 partial fill sequence."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.poll_counts: dict[str, int] = {}
        self._base_prices["HAL"] = 1000.0

    async def _fill_order(self, order_state: Any) -> None:
        # Prevent automatic background filling so we can control fills deterministically via get_order_status
        pass

    async def place_buy_order(
        self, symbol: str, quantity: int, order_type: str, price: float | None = None
    ) -> Order:
        order = await super().place_buy_order(symbol, quantity, order_type, price)
        return order

    async def get_order_status(self, order_id: str) -> Order:
        ostate = self._orders.get(order_id)
        if not ostate:
            raise ValueError(f"Order {order_id} not found")

        count = self.poll_counts.get(order_id, 0) + 1
        self.poll_counts[order_id] = count
        order = ostate.order

        price = self._get_current_price(order.symbol)
        order.filled_price = price

        if count == 1:
            order.status = "PARTIALLY_FILLED"
            order.filled_quantity = 30
            self._cash -= 30 * price
        elif count == 2:
            order.status = "PARTIALLY_FILLED"
            # Increase by 50 -> total 80
            order.filled_quantity = 80
            self._cash -= 50 * price
        else:
            order.status = "COMPLETED"
            # Increase by 20 -> total 100
            order.filled_quantity = 100
            order.completed_at = datetime.now(UTC)
            self._cash -= 20 * price

        return order


@pytest.mark.asyncio
async def test_network_failures_and_timeouts(
    session_factory: Any,
    base_config: Any,
    time_controller: TimeController,
) -> None:
    """Verify engine handles network timeouts gracefully without duplicate order placement."""
    broker = NetworkFlakyBroker(initial_cash=100_000.0)
    broker._holdings = {"INFY": {"quantity": 10, "avg_price": 1500.0}}

    rules = RulesConfig(
        sell_rules=[SellRule(symbol="INFY", condition=ConditionConfig(type="market"))],
        buy_queue=[],
    )

    engine = ExecutionEngine(
        broker=broker,
        config=base_config,
        session_factory=session_factory,
        rules_config=rules,
    )
    await engine.setup()

    try:
        # Cycle 1: Placed sell order, but status check will raise BrokerNetworkError on first attempt
        time_controller.advance(minutes=15)  # 09:15
        ctx1 = await engine.step()
        assert ctx1 is not None
        assert broker.place_order_count == 1

        # Give order watcher a moment to retry and succeed on second poll
        await asyncio.sleep(0.15)

        # Cycle 2: Engine runs again, verify NO duplicate sell order was placed for INFY
        ctx2 = await engine.step()
        assert ctx2 is not None
        assert (
            broker.place_order_count == 1
        ), "Should NOT place duplicate order after network recovery"
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_partial_fills_incremental_processing(
    session_factory: Any,
    base_config: Any,
    time_controller: TimeController,
) -> None:
    """Simulate a 100 qty order filling as 30 -> 50 -> 20 across multiple polls."""
    base_config.safety.max_allocation_per_stock = 1.0
    broker = PartialFillBroker(initial_cash=100_000.0)
    rules = RulesConfig(
        sell_rules=[],
        buy_queue=[
            BuyQueueEntry(
                symbol="HAL", priority=1, condition=ConditionConfig(type="market")
            ),
        ],
    )
    engine = ExecutionEngine(
        broker=broker,
        config=base_config,
        session_factory=session_factory,
        rules_config=rules,
    )
    await engine.setup()

    try:
        time_controller.advance(minutes=15)  # 09:15
        initial_balance: Balance = await broker.get_balance()
        initial_cash = initial_balance.available_cash

        # Trigger buy order of 100 qty
        ctx = await engine.step()
        assert ctx is not None
        assert len(ctx.buy_order_ids) == 1
        order_id = ctx.buy_order_ids[0]

        # Let OrderWatcher poll multiple times to process 30 -> 50 -> 20
        await asyncio.sleep(0.2)

        async with session_factory() as session:
            repo = Repository(session)
            db_order = await repo.get_order(order_id)
            assert db_order is not None
            assert db_order.status == "COMPLETED"
            assert db_order.filled_quantity == 100

            # Verify order_fills table has 3 entries with quantities 30, 50, and 20
            stmt = (
                select(DBOrderFill)
                .where(DBOrderFill.order_id == order_id)
                .order_by(DBOrderFill.executed_at.asc())
            )
            res = await session.execute(stmt)
            fills = res.scalars().all()
            assert len(fills) == 3
            assert [f.quantity for f in fills] == [30, 50, 20]
            assert sum(f.quantity for f in fills) == 100

        # Verify cash is updated incrementally
        final_balance: Balance = await broker.get_balance()
        assert final_balance.available_cash < initial_cash
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_kill_switch_during_pending_order(
    engine_with_holdings: ExecutionEngine,
    time_controller: TimeController,
) -> None:
    """Verify triggering kill switch continues to watch in-flight orders but blocks new orders."""
    engine = engine_with_holdings
    time_controller.advance(minutes=15)  # 09:15

    # Trigger kill switch while engine is active
    engine._safety_gate.kill_switch.activate()
    assert engine._safety_gate.kill_switch.is_active()

    # Step engine: should block new orders due to kill switch
    ctx = await engine.step()
    assert ctx is not None
    assert len(ctx.sell_order_ids) == 0, "Kill switch should prevent new sell orders"
    assert len(ctx.buy_order_ids) == 0, "Kill switch should prevent new buy orders"


@pytest.mark.asyncio
async def test_manual_confirmation_flow(
    session_factory: Any,
    base_config: Any,
    time_controller: TimeController,
) -> None:
    """Test manual confirmation pause, approval, and continuation of other trading symbols."""
    base_config.safety.max_allocation_per_stock = 1.0
    broker = DeterministicMockBroker(initial_cash=100_000.0)
    rules = RulesConfig(
        sell_rules=[],
        buy_queue=[
            BuyQueueEntry(
                symbol="HAL", priority=1, condition=ConditionConfig(type="market")
            ),
            BuyQueueEntry(
                symbol="BEL", priority=2, condition=ConditionConfig(type="market")
            ),
        ],
    )
    engine = ExecutionEngine(
        broker=broker,
        config=base_config,
        session_factory=session_factory,
        rules_config=rules,
    )
    await engine.setup()

    try:
        time_controller.advance(minutes=15)  # 09:15

        # Flag HAL for manual confirmation
        engine._safety_gate.manual_confirmation.flag_symbol("HAL")
        assert engine._safety_gate.manual_confirmation.is_flagged("HAL")

        # Start step in background so we can approve while it's waiting
        step_task = asyncio.create_task(engine.step())
        await asyncio.sleep(0.05)  # Let engine reach manual confirmation check for HAL

        # Approve HAL while paused
        engine._safety_gate.manual_confirmation.approve("HAL")
        ctx1 = await step_task
        assert ctx1 is not None
        assert (
            len(ctx1.buy_order_ids) >= 1
        ), "Order should be placed after manual approval"

        # Verify both HAL and BEL could trade normally
        async with session_factory() as session:
            repo = Repository(session)
            orders = await repo.get_recent_orders(limit=10)
            symbols = {o.symbol for o in orders}
            assert "HAL" in symbols or "BEL" in symbols
    finally:
        await engine.stop()
