from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from portfolio_automation.broker.models import Order as BrokerOrder
from portfolio_automation.core.config import (
    BrokerConfig,
    ConfigModel,
    DatabaseConfig,
    ExecutionConfig,
    SafetyConfig,
)
from portfolio_automation.execution.engine import ExecutionEngine
from portfolio_automation.notifications.interface import EventType
from portfolio_automation.persistence.database import create_engine as create_db_engine
from portfolio_automation.persistence.database import create_session_factory
from portfolio_automation.persistence.models import Base


@pytest.fixture
async def test_env(tmp_path):
    db_path = str(tmp_path / "test_engine_run.db")
    cfg = ConfigModel(
        broker=BrokerConfig(type="mock"),
        execution=ExecutionConfig(
            poll_interval_seconds=0, market_open="09:15", market_close="15:30"
        ),
        safety=SafetyConfig(dry_run=False),
        database=DatabaseConfig(path=db_path),
    )
    db_engine = create_db_engine(cfg)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(db_engine)

    mock_broker = MagicMock()
    mock_broker.authenticate = AsyncMock()
    mock_broker.get_orders = AsyncMock(return_value=[])

    engine = ExecutionEngine(
        broker=mock_broker,
        config=cfg,
        session_factory=session_factory,
    )
    yield engine, mock_broker, cfg
    await db_engine.dispose()


@pytest.mark.asyncio
async def test_engine_run_and_shutdown(test_env):
    engine, mock_broker, cfg = test_env

    # Make step() run twice then trigger shutdown
    count = 0

    async def fake_step():
        nonlocal count
        count += 1
        if count >= 2:
            await engine._shutdown()
        return MagicMock()

    with patch.object(engine, "step", side_effect=fake_step):
        await engine.run()

    assert count == 2
    mock_broker.authenticate.assert_called_once()


@pytest.mark.asyncio
async def test_engine_run_with_step_exception(test_env):
    engine, mock_broker, cfg = test_env
    mock_notifier = MagicMock()
    mock_notifier.fire_and_forget = MagicMock()
    mock_notifier.wait_all = AsyncMock()
    engine._notifier = mock_notifier

    count = 0

    async def fake_step_with_err():
        nonlocal count
        count += 1
        if count == 1:
            raise ValueError("Simulated step failure")
        await engine._shutdown()
        return None

    with patch.object(engine, "step", side_effect=fake_step_with_err):
        await engine.run()

    assert count == 2
    mock_notifier.fire_and_forget.assert_called_with(
        EventType.ERROR,
        {
            "reason": "Simulated step failure",
            "message": "Unhandled error in execution cycle",
        },
    )
    mock_notifier.wait_all.assert_called_once()


@pytest.mark.asyncio
async def test_engine_run_when_step_returns_none(test_env):
    engine, mock_broker, cfg = test_env

    count = 0

    async def fake_step_none():
        nonlocal count
        count += 1
        if count >= 2:
            await engine._shutdown()
        return None

    with patch.object(engine, "step", side_effect=fake_step_none):
        await engine.run()

    assert count == 2


@pytest.mark.asyncio
async def test_authenticate_broker_failure(test_env):
    engine, mock_broker, cfg = test_env
    mock_broker.authenticate.side_effect = RuntimeError("Auth error")

    with pytest.raises(RuntimeError, match="Auth error"):
        await engine._authenticate_broker()


@pytest.mark.asyncio
async def test_reconcile_open_orders_success(test_env):
    engine, mock_broker, cfg = test_env
    now_dt = datetime.now(UTC)
    mock_broker.get_orders.return_value = [
        BrokerOrder(
            id="orphan-1",
            symbol="INFY",
            side="BUY",
            order_type="LIMIT",
            quantity=10,
            price=1500.0,
            status="OPEN",
            filled_quantity=0,
            placed_at=now_dt,
        ),
        BrokerOrder(
            id="closed-1",
            symbol="TCS",
            side="SELL",
            order_type="MARKET",
            quantity=5,
            price=3500.0,
            status="COMPLETE",
            filled_quantity=5,
            placed_at=now_dt,
        ),
    ]

    await engine.setup()
    assert "orphan-1" in engine._active_orders
    assert "closed-1" not in engine._active_orders
    await engine.stop()


@pytest.mark.asyncio
async def test_reconcile_open_orders_failure(test_env):
    engine, mock_broker, cfg = test_env
    mock_broker.get_orders.side_effect = Exception("Broker unreachable")

    # setup() calls _reconcile_open_orders which catches and logs warnings
    await engine.setup()
    await engine.stop()


@pytest.mark.asyncio
async def test_is_market_open(test_env):
    engine, mock_broker, cfg = test_env

    # Override config with valid times
    engine._config.execution.market_open = "00:00"
    engine._config.execution.market_close = "23:59"
    assert await engine._is_market_open() is True

    # Override config with past/future times
    engine._config.execution.market_open = "00:01"
    engine._config.execution.market_close = "00:02"
    with patch("portfolio_automation.execution.engine.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)
        mock_dt.combine = datetime.combine
        assert await engine._is_market_open() is False

    # Override with invalid string
    engine._config.execution.market_open = "invalid"
    assert await engine._is_market_open() is True


@pytest.mark.asyncio
async def test_cleanup_exceptions(test_env):
    engine, mock_broker, cfg = test_env
    mock_watcher = MagicMock()
    mock_watcher.stop = AsyncMock(side_effect=Exception("Watcher error"))

    mock_notifier = MagicMock()
    mock_notifier.wait_all = AsyncMock(side_effect=Exception("Notifier error"))
    engine._notifier = mock_notifier

    mock_session = MagicMock()
    mock_session.close = AsyncMock(side_effect=Exception("Session error"))

    # Should catch and log warnings without raising
    await engine._cleanup(mock_watcher, mock_session)
