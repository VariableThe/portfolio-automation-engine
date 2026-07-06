from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from portfolio_automation.execution.engine import ExecutionEngine
from portfolio_automation.persistence.models import Order as DBOrder
from portfolio_automation.persistence.repository import Repository
from portfolio_automation.rules.models import (
    BuyQueueEntry,
    ConditionConfig,
    RulesConfig,
)
from tests.integration.conftest import DeterministicMockBroker, TimeController


@pytest.mark.asyncio
async def test_cooldown_persistence_across_restarts(
    session_factory: async_sessionmaker,
    base_config: Any,
    time_controller: TimeController,
) -> None:
    """Verify cooldowns persist in the database and remain active after engine restart."""
    broker1 = DeterministicMockBroker(initial_cash=100_000.0)
    engine1 = ExecutionEngine(
        broker=broker1,
        config=base_config,
        session_factory=session_factory,
    )
    await engine1.setup()

    try:
        time_controller.advance(minutes=15)  # 09:15
        # Start a cooldown for INFY
        await engine1._safety_gate.cooldown_manager.start_cooldown(
            "INFY", "sell_order_999"
        )
        reason1 = await engine1._safety_gate.cooldown_manager.check_cooldown("INFY")
        assert reason1 is not None
        assert "Cooldown active for INFY" in reason1
    finally:
        await engine1.stop()

    # Create a completely new engine instance reusing the same DB session factory
    broker2 = DeterministicMockBroker(initial_cash=100_000.0)
    engine2 = ExecutionEngine(
        broker=broker2,
        config=base_config,
        session_factory=session_factory,
    )
    await engine2.setup()

    try:
        # Verify the cooldown is still active in the new engine
        reason2 = await engine2._safety_gate.cooldown_manager.check_cooldown("INFY")
        assert reason2 is not None, "Cooldown must persist across engine restarts"
        assert "Cooldown active for INFY" in reason2

        # Verify buying INFY is blocked by safety gate
        res = await engine2._safety_gate.can_buy("INFY", quantity=10, price=1500.0)
        assert not res.passed
        assert "Cooldown active for INFY" in res.reason
    finally:
        await engine2.stop()


@pytest.mark.asyncio
async def test_daily_limit_reset_at_midnight(
    session_factory: async_sessionmaker,
    base_config: Any,
    time_controller: TimeController,
) -> None:
    """Verify daily limits count trades correctly and reset across midnight (23:59:59 -> 00:00:01)."""
    # Set limit to 2 trades for easy testing
    base_config.safety.max_daily_trades = 2
    broker = DeterministicMockBroker(initial_cash=100_000.0)
    engine = ExecutionEngine(
        broker=broker,
        config=base_config,
        session_factory=session_factory,
    )
    await engine.setup()

    try:
        # Set time to 23:59:59 today
        time_controller.move_to("2026-07-04 23:59:59")

        async with session_factory() as session:
            repo = Repository(session)
            # Insert 2 completed orders at 23:59:59
            for i in range(2):
                order = DBOrder(
                    id=f"order_{i}",
                    symbol="TCS",
                    side="BUY",
                    order_type="MARKET",
                    quantity=10,
                    price=3500.0,
                    status="COMPLETED",
                    placed_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                )
                await repo.save_order(order)

        # Check limit at 23:59:59 -> should be reached (2/2)
        reason = await engine._safety_gate._limits.check_trade_count()
        assert reason is not None
        assert "Daily trade limit reached: 2/2" in reason

        # Fast-forward 2 seconds across midnight to 00:00:01
        time_controller.advance(seconds=2)
        assert datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S") == "2026-07-05 00:00:01"

        # Check limit again -> should reset to 0/2
        reason_after_midnight = await engine._safety_gate._limits.check_trade_count()
        assert (
            reason_after_midnight is None
        ), "Daily trade limit must reset after midnight"
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_allocation_limit_enforcement(
    session_factory: async_sessionmaker,
    base_config: Any,
    time_controller: TimeController,
) -> None:
    """Verify max allocation per stock (e.g. 20%) blocks buy orders exceeding threshold."""
    base_config.safety.max_allocation_per_stock = 0.20  # 20% max allocation
    broker = DeterministicMockBroker(initial_cash=100_000.0)
    broker._base_prices["RELIANCE"] = 2500.0

    rules = RulesConfig(
        sell_rules=[],
        buy_queue=[
            BuyQueueEntry(
                symbol="RELIANCE", priority=1, condition=ConditionConfig(type="market")
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
        portfolio_value = 100_000.0

        # Attempting to buy 10 shares of RELIANCE @ 2500 = 25,000 (25% > 20% limit)
        res = await engine._safety_gate.can_buy(
            symbol="RELIANCE",
            quantity=10,
            price=2500.0,
            portfolio_value=portfolio_value,
        )
        assert not res.passed
        assert "Allocation limit for RELIANCE" in res.reason

        # Run engine cycle: should skip buying RELIANCE due to safety check
        ctx = await engine.step()
        assert ctx is not None
        assert len(ctx.buy_order_ids) == 0, "Should block buy exceeding 20% allocation"

        # Verify decision logged in repository
        async with session_factory() as session:
            repo = Repository(session)
            decisions = await repo.get_recent_decisions(limit=10)
            safety_skips = [
                d
                for d in decisions
                if d.symbol == "RELIANCE" and "SAFETY" in d.decision_type
            ]
            assert len(safety_skips) > 0
            assert "Allocation limit" in safety_skips[0].reason
    finally:
        await engine.stop()
