from __future__ import annotations

import time
import tracemalloc
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from portfolio_automation.execution.engine import ExecutionEngine
from portfolio_automation.rules.models import (
    BuyQueueEntry,
    ConditionConfig,
    RulesConfig,
    SellRule,
)
from tests.integration.conftest import DeterministicMockBroker, TimeController


@pytest.mark.asyncio
async def test_large_scale_cycle_throughput(
    session_factory: async_sessionmaker,
    base_config: Any,
    time_controller: TimeController,
) -> None:
    """Verify engine can evaluate 50 active rules across 50 symbols and execute orders in < 2 seconds."""
    time_controller.advance(minutes=15)  # 09:15 market open

    base_config.safety.max_daily_trades = 1000
    base_config.safety.max_daily_investment = 1_000_000_000.0
    base_config.safety.max_allocation_per_stock = 1.0

    broker = DeterministicMockBroker(
        initial_cash=10_000_000.0,
        latency=0.0,
        fill_delay=0.0,
        partial_fill_enabled=False,
    )

    sell_rules = []
    buy_queue = []

    # Setup 50 symbols (49 sell, 1 buy)
    for i in range(49):
        sym_sell = f"SELL_{i}"
        broker._base_prices[sym_sell] = 100.0 + i
        broker._holdings[sym_sell] = {"quantity": 100, "avg_price": 90.0}
        sell_rules.append(
            SellRule(symbol=sym_sell, condition=ConditionConfig(type="market"))
        )

    for i in range(1):
        sym_buy = f"BUY_{i}"
        broker._base_prices[sym_buy] = 200.0
        buy_queue.append(
            BuyQueueEntry(
                symbol=sym_buy, priority=1, condition=ConditionConfig(type="market")
            )
        )

    rules = RulesConfig(sell_rules=sell_rules, buy_queue=buy_queue)

    engine = ExecutionEngine(
        broker=broker,
        config=base_config,
        session_factory=session_factory,
        rules_config=rules,
    )
    await engine.setup()

    try:
        start_time = time.perf_counter()
        ctx = await engine.step()
        duration = time.perf_counter() - start_time

        assert ctx is not None
        assert len(ctx.sell_order_ids) == 49
        assert len(ctx.buy_order_ids) == 1
        assert duration < 2.0, f"Cycle took {duration:.2f}s, expected < 2.0s"
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_rapid_cycles_memory_stability(
    session_factory: async_sessionmaker,
    base_config: Any,
    time_controller: TimeController,
) -> None:
    """Verify memory usage remains stable (no leaks) over 100 rapid cycles."""
    time_controller.advance(minutes=15)  # 09:15 market open

    broker = DeterministicMockBroker(
        initial_cash=100_000.0,
        latency=0.0,
        fill_delay=0.0,
        partial_fill_enabled=False,
    )
    broker._base_prices["TCS"] = 3500.0

    # Use a condition that evaluates without triggering orders on every cycle to test rapid engine loops
    rules = RulesConfig(
        sell_rules=[],
        buy_queue=[
            BuyQueueEntry(
                symbol="TCS",
                priority=1,
                condition=ConditionConfig(
                    type="rsi", params={"period": 14, "lower": 10}
                ),
            )
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
        tracemalloc.start()
        # Warmup cycles
        for _ in range(10):
            await engine.step()

        snap_before = tracemalloc.take_snapshot()

        # 100 rapid cycles
        for _ in range(100):
            await engine.step()

        snap_after = tracemalloc.take_snapshot()
        stats = snap_after.compare_to(snap_before, "lineno")
        total_growth_bytes = sum(stat.size_diff for stat in stats)
        tracemalloc.stop()

        # Ensure memory growth over 100 cycles is under 10 MB
        max_allowed_growth = 10 * 1024 * 1024
        assert (
            total_growth_bytes < max_allowed_growth
        ), f"Memory leak detected: grew by {total_growth_bytes / 1024 / 1024:.2f} MB"
    finally:
        await engine.stop()
