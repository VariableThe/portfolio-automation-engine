from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from portfolio_automation.broker.models import Balance
from portfolio_automation.core.exceptions import BrokerOrderRejectedError
from portfolio_automation.core.types import OnFailureBehavior
from portfolio_automation.execution.buy_manager import BuyManager
from portfolio_automation.notifications.interface import EventType
from portfolio_automation.rules.models import BuyQueueEntry, ConditionConfig
from portfolio_automation.safety.gate import SafetyCheckResult


@pytest.fixture
def mock_deps():
    broker = MagicMock()
    broker.place_buy_order = AsyncMock()
    repo = MagicMock()
    repo.record_decision = AsyncMock()
    repo.save_order = AsyncMock()
    repo.get_pending_orders = AsyncMock(return_value=[])
    rule_engine = MagicMock()
    safety_gate = MagicMock()
    order_watcher = MagicMock()
    notifier = MagicMock()
    notifier.fire_and_forget = MagicMock()
    return broker, repo, rule_engine, safety_gate, order_watcher, notifier


@pytest.mark.asyncio
async def test_buy_manager_dry_run(mock_deps):
    broker, repo, rule_engine, safety_gate, order_watcher, notifier = mock_deps

    rule_engine.evaluate_buy_condition.return_value = MagicMock(
        met=True, reason="RSI low"
    )
    safety_gate.can_buy = AsyncMock(
        return_value=SafetyCheckResult(
            passed=True, reason="Dry run OK", is_dry_run=True
        )
    )

    bm = BuyManager(
        broker=broker,
        repository=repo,
        rule_engine=rule_engine,
        safety_gate=safety_gate,
        order_watcher=order_watcher,
        active_orders=set(),
        notifier=notifier,
        on_failure="skip",
    )

    queue = [
        BuyQueueEntry(
            symbol="INFY",
            priority=1,
            condition=ConditionConfig(type="price_above", value=1000.0),
        )
    ]

    placed = await bm.process_buy_queue(
        buy_queue=queue,
        balance=Balance(available_cash=50000.0, total_cash=50000.0, used_cash=0.0),
        prices={"INFY": 1500.0},
        holdings=[],
        cycle_id="cycle-dry-1",
    )

    assert len(placed) == 0
    broker.place_buy_order.assert_not_called()
    repo.record_decision.assert_called_with(
        decision_type="BUY_DRY_RUN",
        symbol="INFY",
        reason="Would buy 33 shares at 1500.00",
        cycle_id="cycle-dry-1",
    )
    repo.save_order.assert_called_once()
    notifier.fire_and_forget.assert_called_with(
        EventType.ORDER_PLACED,
        {
            "symbol": "INFY",
            "quantity": 33,
            "price": 1500.0,
            "side": "BUY",
            "total": 33 * 1500.0,
            "is_dry_run": True,
        },
    )


@pytest.mark.asyncio
async def test_buy_manager_stop_on_no_price(mock_deps):
    broker, repo, rule_engine, safety_gate, order_watcher, notifier = mock_deps

    bm = BuyManager(
        broker=broker,
        repository=repo,
        rule_engine=rule_engine,
        safety_gate=safety_gate,
        order_watcher=order_watcher,
        active_orders=set(),
        notifier=notifier,
        on_failure=OnFailureBehavior.STOP,
    )

    queue = [
        BuyQueueEntry(
            symbol="INFY",
            priority=1,
            condition=ConditionConfig(type="price_above", value=1000.0),
        ),
        BuyQueueEntry(
            symbol="TCS",
            priority=2,
            condition=ConditionConfig(type="price_above", value=3000.0),
        ),
    ]

    placed = await bm.process_buy_queue(
        buy_queue=queue,
        balance=Balance(available_cash=50000.0, total_cash=50000.0, used_cash=0.0),
        prices={},  # No prices available -> should stop on INFY
        holdings=[],
        cycle_id="cycle-stop-1",
    )

    assert len(placed) == 0
    repo.record_decision.assert_called_once_with(
        decision_type="BUY_SKIP_NO_PRICE",
        symbol="INFY",
        reason="No price available",
        cycle_id="cycle-stop-1",
    )


@pytest.mark.asyncio
async def test_buy_manager_stop_on_insufficient_cash(mock_deps):
    broker, repo, rule_engine, safety_gate, order_watcher, notifier = mock_deps

    bm = BuyManager(
        broker=broker,
        repository=repo,
        rule_engine=rule_engine,
        safety_gate=safety_gate,
        order_watcher=order_watcher,
        active_orders=set(),
        notifier=notifier,
        on_failure=OnFailureBehavior.STOP,
    )

    queue = [
        BuyQueueEntry(
            symbol="MRF",
            priority=1,
            condition=ConditionConfig(type="price_above", value=1000.0),
        ),
        BuyQueueEntry(
            symbol="TCS",
            priority=2,
            condition=ConditionConfig(type="price_above", value=3000.0),
        ),
    ]

    placed = await bm.process_buy_queue(
        buy_queue=queue,
        balance=Balance(
            available_cash=500.0, total_cash=500.0, used_cash=0.0
        ),  # MRF price is 100,000 -> insufficient cash -> stop
        prices={"MRF": 100000.0, "TCS": 3500.0},
        holdings=[],
        cycle_id="cycle-stop-2",
    )

    assert len(placed) == 0
    repo.record_decision.assert_called_once_with(
        decision_type="BUY_SKIP_CASH",
        symbol="MRF",
        reason="Insufficient cash: need 100000.00, have 500.00",
        cycle_id="cycle-stop-2",
    )


@pytest.mark.asyncio
async def test_buy_manager_stop_on_safety_blocked(mock_deps):
    broker, repo, rule_engine, safety_gate, order_watcher, notifier = mock_deps

    rule_engine.evaluate_buy_condition.return_value = MagicMock(
        met=True, reason="RSI low"
    )
    safety_gate.can_buy = AsyncMock(
        return_value=SafetyCheckResult(passed=False, reason="Daily limit reached")
    )

    bm = BuyManager(
        broker=broker,
        repository=repo,
        rule_engine=rule_engine,
        safety_gate=safety_gate,
        order_watcher=order_watcher,
        active_orders=set(),
        notifier=notifier,
        on_failure=OnFailureBehavior.STOP,
    )

    queue = [
        BuyQueueEntry(
            symbol="INFY",
            priority=1,
            condition=ConditionConfig(type="price_above", value=1000.0),
        ),
        BuyQueueEntry(
            symbol="TCS",
            priority=2,
            condition=ConditionConfig(type="price_above", value=3000.0),
        ),
    ]

    placed = await bm.process_buy_queue(
        buy_queue=queue,
        balance=Balance(available_cash=50000.0, total_cash=50000.0, used_cash=0.0),
        prices={"INFY": 1500.0, "TCS": 3500.0},
        holdings=[],
        cycle_id="cycle-stop-3",
    )

    assert len(placed) == 0
    repo.record_decision.assert_called_once_with(
        decision_type="BUY_BLOCKED_SAFETY",
        symbol="INFY",
        reason="Daily limit reached",
        cycle_id="cycle-stop-3",
    )
    notifier.fire_and_forget.assert_called_with(
        EventType.SAFETY_TRIGGERED,
        {"symbol": "INFY", "reason": "Daily limit reached", "side": "BUY"},
    )


@pytest.mark.asyncio
async def test_buy_manager_stop_on_order_rejected(mock_deps):
    broker, repo, rule_engine, safety_gate, order_watcher, notifier = mock_deps

    rule_engine.evaluate_buy_condition.return_value = MagicMock(
        met=True, reason="RSI low"
    )
    safety_gate.can_buy = AsyncMock(
        return_value=SafetyCheckResult(passed=True, reason="OK", is_dry_run=False)
    )
    broker.place_buy_order.side_effect = BrokerOrderRejectedError("Margin short")

    bm = BuyManager(
        broker=broker,
        repository=repo,
        rule_engine=rule_engine,
        safety_gate=safety_gate,
        order_watcher=order_watcher,
        active_orders=set(),
        notifier=notifier,
        on_failure=OnFailureBehavior.STOP,
    )

    queue = [
        BuyQueueEntry(
            symbol="INFY",
            priority=1,
            condition=ConditionConfig(type="price_above", value=1000.0),
        ),
        BuyQueueEntry(
            symbol="TCS",
            priority=2,
            condition=ConditionConfig(type="price_above", value=3000.0),
        ),
    ]

    placed = await bm.process_buy_queue(
        buy_queue=queue,
        balance=Balance(available_cash=50000.0, total_cash=50000.0, used_cash=0.0),
        prices={"INFY": 1500.0, "TCS": 3500.0},
        holdings=[],
        cycle_id="cycle-stop-4",
    )

    assert len(placed) == 0
    repo.record_decision.assert_called_once_with(
        decision_type="BUY_REJECTED",
        symbol="INFY",
        reason="Margin short",
        cycle_id="cycle-stop-4",
    )
    notifier.fire_and_forget.assert_called_with(
        EventType.ORDER_REJECTED,
        {"symbol": "INFY", "reason": "Margin short", "side": "BUY"},
    )


@pytest.mark.asyncio
async def test_buy_manager_no_remaining_cash(mock_deps):
    broker, repo, rule_engine, safety_gate, order_watcher, notifier = mock_deps

    bm = BuyManager(
        broker=broker,
        repository=repo,
        rule_engine=rule_engine,
        safety_gate=safety_gate,
        order_watcher=order_watcher,
        active_orders=set(),
        notifier=notifier,
        on_failure="skip",
    )

    queue = [
        BuyQueueEntry(
            symbol="INFY",
            priority=1,
            condition=ConditionConfig(type="price_above", value=1000.0),
        )
    ]

    placed = await bm.process_buy_queue(
        buy_queue=queue,
        balance=Balance(
            available_cash=0.0, total_cash=0.0, used_cash=0.0
        ),  # Zero cash -> immediate break
        prices={"INFY": 1500.0},
        holdings=[],
        cycle_id="cycle-zero-cash",
    )

    assert len(placed) == 0
    rule_engine.evaluate_buy_condition.assert_not_called()
