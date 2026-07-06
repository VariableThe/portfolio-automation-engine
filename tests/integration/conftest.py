from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

import freezegun
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from portfolio_automation.broker.mock import MockBroker
from portfolio_automation.core.config import (
    ConfigModel,
    ConsoleChannelConfig,
    DatabaseConfig,
    ExecutionConfig,
    LoggingConfig,
    NotificationChannelsConfig,
    NotificationConfig,
    SafetyConfig,
)
from portfolio_automation.core.exceptions import BrokerSymbolNotFoundError
from portfolio_automation.execution.engine import ExecutionEngine
from portfolio_automation.notifications.manager import NotificationManager
from portfolio_automation.persistence.models import Base
from portfolio_automation.rules.models import (
    BuyQueueEntry,
    ConditionConfig,
    RulesConfig,
    SellRule,
)


class TimeController:
    """Controllable time fixture using freezegun for deterministic time manipulation without asyncio.sleep()."""

    def __init__(self, start_time: str = "2026-07-04 09:15:00") -> None:
        self.freezer = freezegun.freeze_time(
            start_time, ignore=["asyncio", "threading"]
        )
        self.frozen_time = self.freezer.start()
        self.current_dt = datetime.fromisoformat(start_time).replace(tzinfo=UTC)

    def advance(
        self,
        hours: float = 0,
        minutes: float = 0,
        seconds: float = 0,
        days: float = 0,
    ) -> None:
        delta = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        self.frozen_time.tick(delta=delta)
        self.current_dt += delta

    def move_to(self, target_time: str) -> None:
        self.frozen_time.move_to(target_time)
        self.current_dt = datetime.fromisoformat(target_time).replace(tzinfo=UTC)

    def stop(self) -> None:
        self.freezer.stop()


class DeterministicMockBroker(MockBroker):
    """MockBroker with fixed seed random generator for reproducible integration testing."""

    def __init__(self, seed: int = 42, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._rng = random.Random(seed)

    def _get_current_price(self, symbol: str) -> float:
        base = self._base_prices.get(symbol)
        if base is None:
            raise BrokerSymbolNotFoundError(
                f"Symbol '{symbol}' not found in mock data."
            )
        fluctuation = base * self._rng.uniform(-0.01, 0.01)
        return round(base + fluctuation, 2)


class HighVolatilityMockBroker(MockBroker):
    """Second mock broker with wider price swings and slight latency for compatibility testing."""

    def __init__(self, seed: int = 99, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._rng = random.Random(seed)

    def _get_current_price(self, symbol: str) -> float:
        base = self._base_prices.get(symbol)
        if base is None:
            raise BrokerSymbolNotFoundError(
                f"Symbol '{symbol}' not found in mock data."
            )
        fluctuation = base * self._rng.uniform(-0.05, 0.05)
        return round(base + fluctuation, 2)


@pytest.fixture(name="time_controller")
def time_controller_fixture() -> Any:
    tc = TimeController("2026-07-04 09:15:00")
    yield tc
    tc.stop()


@pytest_asyncio.fixture(name="db_engine")
async def db_engine_fixture() -> Any:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture(name="session_factory")
def session_factory_fixture(db_engine: AsyncEngine) -> Any:
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(name="broker")
def broker_fixture() -> DeterministicMockBroker:
    return DeterministicMockBroker(
        initial_cash=100_000.0,
        latency=0.0,
        fill_delay=0.0,
        partial_fill_enabled=False,
        reject_probability=0.0,
    )


@pytest.fixture(name="base_config")
def base_config_fixture() -> ConfigModel:
    return ConfigModel(
        execution=ExecutionConfig(
            poll_interval_seconds=60,
            market_open="09:15",
            market_close="15:30",
            buy_queue_on_failure="skip",
        ),
        safety=SafetyConfig(
            dry_run=False,
            max_daily_trades=100,
            max_daily_investment=1_000_000.0,
            max_allocation_per_stock=1.0,
            cooldown_minutes=30,
            manual_confirmation=False,
            kill_switch_enabled=True,
        ),
        database=DatabaseConfig(path=":memory:"),
        logging=LoggingConfig(level="INFO", format="console"),
        notifications=NotificationConfig(
            enabled=True,
            channels=NotificationChannelsConfig(
                console=ConsoleChannelConfig(enabled=True)
            ),
        ),
    )


@pytest_asyncio.fixture(name="engine_with_holdings")
async def engine_with_holdings_fixture(
    broker: DeterministicMockBroker,
    session_factory: Any,
    base_config: ConfigModel,
    time_controller: TimeController,
) -> Any:
    broker._holdings = {
        "INFY": {"quantity": 10, "avg_price": 1500.0},
        "TCS": {"quantity": 5, "avg_price": 3500.0},
        "RELIANCE": {"quantity": 8, "avg_price": 2500.0},
        "HDFC": {"quantity": 15, "avg_price": 1600.0},
        "WIPRO": {"quantity": 20, "avg_price": 450.0},
    }

    broker._base_prices["HAL"] = 3500.0
    broker._base_prices["BEL"] = 1500.0

    rules = RulesConfig(
        sell_rules=[
            SellRule(symbol="INFY", condition=ConditionConfig(type="market")),
            SellRule(symbol="TCS", condition=ConditionConfig(type="market")),
        ],
        buy_queue=[
            BuyQueueEntry(
                symbol="HAL",
                priority=1,
                condition=ConditionConfig(type="price_above", value=4000.0),
            ),
            BuyQueueEntry(
                symbol="BEL",
                priority=2,
                condition=ConditionConfig(type="price_above", value=2000.0),
            ),
        ],
    )

    notifier = NotificationManager.from_config(base_config.notifications)
    engine = ExecutionEngine(
        broker=broker,
        config=base_config,
        session_factory=session_factory,
        rules_config=rules,
        notifier=notifier,
    )
    await engine.setup()
    yield engine
    await engine.stop()


@pytest_asyncio.fixture(name="engine_with_daily_limits")
async def engine_with_daily_limits_fixture(
    broker: DeterministicMockBroker,
    session_factory: Any,
    base_config: ConfigModel,
    time_controller: TimeController,
) -> Any:
    base_config.safety.max_daily_trades = 2
    base_config.safety.max_daily_investment = 10_000.0
    base_config.safety.cooldown_minutes = 60

    broker._holdings = {
        "INFY": {"quantity": 10, "avg_price": 1500.0},
    }

    rules = RulesConfig(
        sell_rules=[
            SellRule(symbol="INFY", condition=ConditionConfig(type="market")),
        ],
        buy_queue=[
            BuyQueueEntry(
                symbol="BEL", priority=1, condition=ConditionConfig(type="market")
            ),
            BuyQueueEntry(
                symbol="WIPRO", priority=2, condition=ConditionConfig(type="market")
            ),
            BuyQueueEntry(
                symbol="HAL", priority=3, condition=ConditionConfig(type="market")
            ),
        ],
    )

    notifier = NotificationManager.from_config(base_config.notifications)
    engine = ExecutionEngine(
        broker=broker,
        config=base_config,
        session_factory=session_factory,
        rules_config=rules,
        notifier=notifier,
    )
    await engine.setup()
    yield engine
    await engine.stop()
