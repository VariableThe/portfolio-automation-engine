from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from portfolio_automation.execution.engine import ExecutionEngine
from portfolio_automation.persistence.repository import Repository
from portfolio_automation.rules.models import (
    BuyQueueEntry,
    ConditionConfig,
    RulesConfig,
)
from tests.integration.conftest import DeterministicMockBroker, TimeController


class SecondMockBroker(DeterministicMockBroker):
    """A second broker implementation simulating different latency and slight price slippage/differences."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Slightly different base pricing for SecondMockBroker
        self._base_prices["TCS"] = 3550.0
        self._base_prices["INFY"] = 1420.0
        self.latency_ms = 10.0

    async def _simulate_latency(self) -> None:
        if self.latency_ms > 0:
            await asyncio.sleep(self.latency_ms / 1000.0)

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"MOCK2_{datetime.now(UTC).strftime('%y%m%d')}{self._order_counter:06d}"


@pytest.mark.asyncio
async def test_multi_broker_seamless_swap(
    session_factory: async_sessionmaker,
    base_config: Any,
    time_controller: TimeController,
) -> None:
    """Verify execution engine can switch from Broker A to Broker B seamlessly without breaking business logic or DB state."""
    time_controller.advance(minutes=15)  # 09:15 market open

    # ── Phase 1: Run cycle with Broker A ────────────────────────────────────────
    broker_a = DeterministicMockBroker(
        initial_cash=100_000.0, partial_fill_enabled=False, fill_delay=0.01
    )
    broker_a._base_prices["TCS"] = 3500.0

    rules_a = RulesConfig(
        sell_rules=[],
        buy_queue=[
            BuyQueueEntry(
                symbol="TCS", priority=1, condition=ConditionConfig(type="market")
            ),
        ],
    )
    engine_a = ExecutionEngine(
        broker=broker_a,
        config=base_config,
        session_factory=session_factory,
        rules_config=rules_a,
    )
    await engine_a.setup()

    try:
        ctx_a = await engine_a.step()
        assert ctx_a is not None
        assert len(ctx_a.buy_order_ids) == 1
        order_id_a = ctx_a.buy_order_ids[0]
        await asyncio.sleep(0.2)

        # Verify order from Broker A is completed and in DB
        async with session_factory() as session:
            repo = Repository(session)
            db_order_a = await repo.get_order(order_id_a)
            assert db_order_a is not None
            assert db_order_a.status == "COMPLETED"
            assert db_order_a.symbol == "TCS"
    finally:
        await engine_a.stop()

    # ── Phase 2: Switch to Broker B and run cycle ────────────────────────────────
    broker_b = SecondMockBroker(
        initial_cash=100_000.0, partial_fill_enabled=False, fill_delay=0.01
    )

    rules_b = RulesConfig(
        sell_rules=[],
        buy_queue=[
            BuyQueueEntry(
                symbol="WIPRO", priority=1, condition=ConditionConfig(type="market")
            ),
        ],
    )
    engine_b = ExecutionEngine(
        broker=broker_b,
        config=base_config,
        session_factory=session_factory,
        rules_config=rules_b,
    )
    await engine_b.setup()

    try:
        ctx_b = await engine_b.step()
        assert ctx_b is not None
        assert len(ctx_b.buy_order_ids) == 1
        order_id_b = ctx_b.buy_order_ids[0]
        await asyncio.sleep(0.2)

        # Verify order from Broker B is completed and stored in the same DB
        async with session_factory() as session:
            repo = Repository(session)
            db_order_b = await repo.get_order(order_id_b)
            assert db_order_b is not None
            assert db_order_b.status == "COMPLETED"
            assert db_order_b.symbol == "WIPRO"

            # Verify total DB state contains orders from both brokers seamlessly
            all_orders = await repo.get_recent_orders(limit=10)
            order_symbols = {o.symbol for o in all_orders}
            assert "TCS" in order_symbols and "WIPRO" in order_symbols
            assert len(all_orders) == 2
    finally:
        await engine_b.stop()
