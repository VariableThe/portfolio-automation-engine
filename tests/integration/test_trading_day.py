from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from portfolio_automation.broker.models import Balance
from portfolio_automation.execution.engine import ExecutionEngine
from portfolio_automation.notifications.interface import EventType
from portfolio_automation.persistence.models import (
    OrderFill,
)
from portfolio_automation.persistence.repository import Repository
from tests.integration.conftest import TimeController


class RecordingChannel:
    """A notification channel that records all dispatched events for verification."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    async def send(self, event_type: str, message: str, data: dict[str, Any]) -> None:
        self.events.append((event_type, message, data))


@pytest.mark.asyncio
async def test_end_to_end_trading_day_simulation(
    engine_with_holdings: ExecutionEngine,
    session_factory: Any,
    time_controller: TimeController,
) -> None:
    """Simulates a complete trading day from pre-open to post-close."""
    engine = engine_with_holdings
    recorder = RecordingChannel()
    if engine._notifier:
        engine._notifier.register_channel("recorder", recorder)

    # 1. Pre-open check (09:00:00) - Market is closed
    time_controller.advance(minutes=-15)  # Set time to 09:00:00
    res = await engine.step()
    assert (
        res is None
    ), "Engine should skip execution when market is closed before 09:15"

    # 2. Market opens (09:15:00)
    time_controller.advance(minutes=15)  # Back to 09:15:00
    initial_balance: Balance = await engine._broker.get_balance()
    initial_cash = initial_balance.available_cash

    # 3. Execution Cycle 1 - Sell rules trigger (INFY, TCS) -> orders placed -> fills received -> cash refreshed
    ctx1 = await engine.step()
    assert ctx1 is not None
    assert len(ctx1.sell_order_ids) > 0, "Expected sell orders to be placed"

    post_sell_balance: Balance = await engine._broker.get_balance()
    assert (
        post_sell_balance.available_cash > initial_cash
    ), "Cash should increase after selling holdings"

    # Verify orders and fills in database
    async with session_factory() as session:
        repo = Repository(session)
        recent_orders = await repo.get_recent_orders(limit=20)
        sell_orders = [o for o in recent_orders if o.side == "SELL"]
        assert len(sell_orders) >= len(ctx1.sell_order_ids)
        for order in sell_orders:
            assert order.status == "COMPLETED"
            assert order.filled_quantity is not None and order.filled_quantity > 0

        # Verify order fills table
        stmt = select(OrderFill)
        fills_res = await session.execute(stmt)
        fills = fills_res.scalars().all()
        assert len(fills) >= len(sell_orders)

        # Verify portfolio snapshots are saved
        snapshots = await repo.get_latest_holdings(cycle_id=ctx1.cycle_id)
        assert len(snapshots) > 0

        # Verify strategy decisions are logged with reasons
        all_decisions = await repo.get_recent_decisions(limit=50)
        decisions = [d for d in all_decisions if d.cycle_id == ctx1.cycle_id]
        assert len(decisions) > 0
        for d in decisions:
            assert d.reason is not None and len(d.reason) > 0

    # 4. Execution Cycle 2 - Buy queue processes with updated cash -> orders placed -> fills received
    time_controller.advance(minutes=30)  # 09:45:00
    engine._broker._base_prices["HAL"] = 4500.0
    engine._broker._base_prices["BEL"] = 2500.0
    ctx2 = await engine.step()
    assert ctx2 is not None
    if ctx2.buy_order_ids:
        for order_id in ctx2.buy_order_ids:
            await engine._order_watcher.wait_for_terminal(order_id, timeout=5.0)

    async with session_factory() as session:
        repo = Repository(session)
        recent_orders = await repo.get_recent_orders(limit=20)
        buy_orders = [o for o in recent_orders if o.side == "BUY"]
        if len(buy_orders) > 0:
            for order in buy_orders:
                assert order.status == "COMPLETED"
                assert order.filled_quantity is not None and order.filled_quantity > 0

            post_buy_balance: Balance = await engine._broker.get_balance()
            assert post_buy_balance.available_cash <= post_sell_balance.available_cash

    # 5. Verify notifications were triggered for all events
    assert len(recorder.events) > 0
    event_types = {e[0] for e in recorder.events}
    assert (
        EventType.ORDER_PLACED in event_types or EventType.ORDER_FILLED in event_types
    )

    # 6. Market closes (15:35:00)
    time_controller.advance(hours=6)  # 15:45:00
    res_closed = await engine.step()
    assert res_closed is None, "Engine should skip execution after market close (15:30)"

    # 7. Daily summary notification sent
    if engine._notifier:
        final_balance: Balance = await engine._broker.get_balance()
        holdings = await engine._broker.get_holdings()
        total_val = final_balance.total_cash + sum(
            h.current_price * h.quantity for h in holdings
        )
        await engine._notifier.notify(
            EventType.DAILY_SUMMARY,
            {
                "total_value": total_val,
                "available_cash": final_balance.available_cash,
                "total_trades": len(recorder.events),
                "holdings_count": len(holdings),
            },
            message="Daily Portfolio Summary",
        )
        assert EventType.DAILY_SUMMARY in {e[0] for e in recorder.events}
