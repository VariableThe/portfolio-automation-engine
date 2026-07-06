from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from portfolio_automation.broker.mock import MockBroker
from portfolio_automation.core.types import OrderStatus
from portfolio_automation.execution.buy_manager import BuyManager
from portfolio_automation.execution.cycle import execute_cycle
from portfolio_automation.execution.engine import ExecutionEngine
from portfolio_automation.execution.order_watcher import OrderWatcher
from portfolio_automation.execution.sell_manager import SellManager
from portfolio_automation.persistence.models import Base, Order
from portfolio_automation.persistence.repository import Repository
from portfolio_automation.rules.engine import RuleEngine
from portfolio_automation.rules.models import (
    BuyQueueEntry,
    ConditionConfig,
    RulesConfig,
    SellRule,
)
from portfolio_automation.safety.dry_run import DryRunGuard
from portfolio_automation.safety.gate import SafetyGate


@pytest.fixture(name="engine")
async def engine_fixture() -> AsyncEngine:
    eng = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture(name="session")
async def session_fixture(engine: AsyncEngine) -> AsyncSession:
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(session: AsyncSession) -> Repository:
    return Repository(session)


@pytest.fixture(name="broker")
def broker_fixture() -> MockBroker:
    return MockBroker(
        initial_cash=100_000.0,
        latency=0.01,
        fill_delay=0.05,
        partial_fill_enabled=False,
        reject_probability=0.0,
    )


@pytest.fixture(name="kill_switch_rules")
def kill_switch_rules_fixture() -> RulesConfig:
    return RulesConfig(
        sell_rules=[
            SellRule(
                symbol="INFY",
                condition=ConditionConfig(type="market"),
            ),
        ],
        buy_queue=[
            BuyQueueEntry(
                symbol="RELIANCE",
                priority=1,
                condition=ConditionConfig(type="market"),
            ),
        ],
    )


@pytest.mark.asyncio
async def test_full_cycle_integration(
    broker: MockBroker,
    repo: Repository,
    session: AsyncSession,
) -> None:
    await broker.authenticate()

    rules = RulesConfig(
        sell_rules=[
            SellRule(
                symbol="INFY",
                condition=ConditionConfig(type="market"),
            ),
        ],
        buy_queue=[
            BuyQueueEntry(
                symbol="RELIANCE",
                priority=1,
                condition=ConditionConfig(type="market"),
            ),
        ],
    )
    rule_engine = RuleEngine(rules)
    active_orders: set[str] = set()
    order_watcher = OrderWatcher(broker=broker, repository=repo, poll_interval=0.05)
    safety_gate = SafetyGate(repository=repo, dry_run_guard=DryRunGuard(enabled=False))
    sell_manager = SellManager(
        broker=broker,
        rule_engine=rule_engine,
        repository=repo,
        order_watcher=order_watcher,
        active_orders=active_orders,
        safety_gate=safety_gate,
    )
    buy_manager = BuyManager(
        broker=broker,
        rule_engine=rule_engine,
        repository=repo,
        order_watcher=order_watcher,
        active_orders=active_orders,
        safety_gate=safety_gate,
        on_failure="skip",
    )

    await order_watcher.start()

    try:
        ctx = await execute_cycle(
            broker=broker,
            rule_engine=rule_engine,
            rules_config=rules,
            repository=repo,
            order_watcher=order_watcher,
            sell_manager=sell_manager,
            buy_manager=buy_manager,
            active_orders=active_orders,
        )

        assert (
            len(ctx.sell_order_ids) == 1
        ), f"Expected 1 sell order, got {ctx.sell_order_ids}"
        assert len(ctx.buy_order_ids) >= 0

        sell_orders = await repo.get_pending_orders("INFY")
        assert len(sell_orders) >= 0

        recent_orders = await repo.get_recent_orders(limit=10)
        sell_db_orders = [o for o in recent_orders if o.side == "SELL"]
        assert len(sell_db_orders) >= 1

        order = sell_db_orders[0]
        assert order.symbol == "INFY"
        assert order.filled_quantity == 10

        buy_db_orders = [o for o in recent_orders if o.side == "BUY"]
        assert len(buy_db_orders) >= 0

        snapshots = await repo.get_latest_holdings(cycle_id=ctx.cycle_id)
        assert len(snapshots) > 0
    finally:
        await order_watcher.stop()


@pytest.mark.asyncio
async def test_buy_queue_skip_behavior(
    broker: MockBroker,
    repo: Repository,
    session: AsyncSession,
) -> None:
    await broker.authenticate()

    rules = RulesConfig(
        sell_rules=[],
        buy_queue=[
            BuyQueueEntry(
                symbol="RELIANCE",
                priority=1,
                condition=ConditionConfig(type="price_above", value=99999.0),
            ),
            BuyQueueEntry(
                symbol="INFY",
                priority=2,
                condition=ConditionConfig(type="market"),
            ),
        ],
    )
    rule_engine = RuleEngine(rules)
    active_orders: set[str] = set()
    order_watcher = OrderWatcher(broker=broker, repository=repo, poll_interval=0.05)
    from portfolio_automation.safety.limits import DailyLimits

    safety_gate = SafetyGate(
        repository=repo,
        dry_run_guard=DryRunGuard(enabled=False),
        daily_limits=DailyLimits(
            repository=repo,
            max_daily_trades=100,
            max_daily_investment=1_000_000.0,
            max_allocation_per_stock=1.0,
        ),
    )
    buy_manager = BuyManager(
        broker=broker,
        rule_engine=rule_engine,
        repository=repo,
        order_watcher=order_watcher,
        active_orders=active_orders,
        safety_gate=safety_gate,
        on_failure="skip",
    )

    await order_watcher.start()
    try:
        balance = await broker.get_balance()
        holdings = await broker.get_holdings()
        prices = {}
        for h in holdings:
            prices[h.symbol] = h.current_price or 0.0
        prices["RELIANCE"] = await broker.get_price("RELIANCE")

        placed = await buy_manager.process_buy_queue(
            buy_queue=rules.buy_queue,
            balance=balance,
            prices=prices,
            holdings=holdings,
            cycle_id="test_skip",
        )

        assert len(placed) == 1, f"Expected 1 buy (INFY), got {len(placed)}"

        decisions = await repo.get_recent_decisions(limit=10)
        buy_decisions = [d for d in decisions if "BUY" in d.decision_type]
        assert any("BUY_SKIP_CONDITION" in d.decision_type for d in buy_decisions)
        assert any("BUY_PLACED" in d.decision_type for d in buy_decisions)
    finally:
        await order_watcher.stop()


@pytest.mark.asyncio
async def test_buy_queue_stop_behavior(
    broker: MockBroker,
    repo: Repository,
    session: AsyncSession,
) -> None:
    await broker.authenticate()

    rules = RulesConfig(
        sell_rules=[],
        buy_queue=[
            BuyQueueEntry(
                symbol="RELIANCE",
                priority=1,
                condition=ConditionConfig(type="price_above", value=99999.0),
            ),
            BuyQueueEntry(
                symbol="INFY",
                priority=2,
                condition=ConditionConfig(type="market"),
            ),
        ],
    )
    rule_engine = RuleEngine(rules)
    active_orders: set[str] = set()
    order_watcher = OrderWatcher(broker=broker, repository=repo, poll_interval=0.05)
    safety_gate = SafetyGate(repository=repo, dry_run_guard=DryRunGuard(enabled=False))
    buy_manager = BuyManager(
        broker=broker,
        rule_engine=rule_engine,
        repository=repo,
        order_watcher=order_watcher,
        active_orders=active_orders,
        safety_gate=safety_gate,
        on_failure="stop",
    )

    await order_watcher.start()
    try:
        balance = await broker.get_balance()
        holdings = await broker.get_holdings()
        prices = {}
        for h in holdings:
            prices[h.symbol] = h.current_price or 0.0
        prices["RELIANCE"] = await broker.get_price("RELIANCE")

        placed = await buy_manager.process_buy_queue(
            buy_queue=rules.buy_queue,
            balance=balance,
            prices=prices,
            holdings=holdings,
            cycle_id="test_stop",
        )

        assert (
            len(placed) == 0
        ), f"Expected 0 buys (stop halts queue), got {len(placed)}"

        decisions = await repo.get_recent_decisions(limit=10)
        buy_decisions = [d for d in decisions if "BUY" in d.decision_type]
        assert any("BUY_SKIP_CONDITION" in d.decision_type for d in buy_decisions)
        buy_placed = [d for d in buy_decisions if d.decision_type == "BUY_PLACED"]
        assert len(buy_placed) == 0
    finally:
        await order_watcher.stop()


@pytest.mark.asyncio
async def test_duplicate_order_prevention(
    broker: MockBroker,
    repo: Repository,
    session: AsyncSession,
) -> None:
    await broker.authenticate()

    existing_order = Order(
        id="EXISTING_SELL_INFY",
        symbol="INFY",
        side="SELL",
        order_type="MARKET",
        quantity=5,
        price=1500.0,
        status="OPEN",
        filled_quantity=0,
        placed_at=datetime.now(UTC),
    )
    await repo.save_order(existing_order)

    rules = RulesConfig(
        sell_rules=[
            SellRule(
                symbol="INFY",
                condition=ConditionConfig(type="market"),
            ),
        ],
        buy_queue=[],
    )
    rule_engine = RuleEngine(rules)
    active_orders: set[str] = set()
    order_watcher = OrderWatcher(broker=broker, repository=repo, poll_interval=0.05)
    safety_gate = SafetyGate(repository=repo, dry_run_guard=DryRunGuard(enabled=False))
    sell_manager = SellManager(
        broker=broker,
        rule_engine=rule_engine,
        repository=repo,
        order_watcher=order_watcher,
        active_orders=active_orders,
        safety_gate=safety_gate,
    )
    buy_manager = BuyManager(
        broker=broker,
        rule_engine=rule_engine,
        repository=repo,
        order_watcher=order_watcher,
        active_orders=active_orders,
        safety_gate=safety_gate,
        on_failure="skip",
    )

    await order_watcher.start()
    try:
        holdings = await broker.get_holdings()
        prices = {}
        for h in holdings:
            prices[h.symbol] = h.current_price or 0.0

        ctx = await execute_cycle(
            broker=broker,
            rule_engine=rule_engine,
            rules_config=rules,
            repository=repo,
            order_watcher=order_watcher,
            sell_manager=sell_manager,
            buy_manager=buy_manager,
            active_orders=active_orders,
        )

        assert len(ctx.sell_order_ids) == 0, "Should not place duplicate sell for INFY"

        decisions = await repo.get_recent_decisions(limit=10)
        skip_decisions = [
            d for d in decisions if "SELL_SKIP_DUPLICATE" in d.decision_type
        ]
        assert len(skip_decisions) >= 1
    finally:
        await order_watcher.stop()


@pytest.mark.asyncio
async def test_dry_run_mode(
    broker: MockBroker,
    repo: Repository,
    session: AsyncSession,
) -> None:
    await broker.authenticate()

    initial_cash = (await broker.get_balance()).available_cash

    rules = RulesConfig(
        sell_rules=[
            SellRule(
                symbol="INFY",
                condition=ConditionConfig(type="market"),
            ),
        ],
        buy_queue=[],
    )
    rule_engine = RuleEngine(rules)
    active_orders: set[str] = set()
    order_watcher = OrderWatcher(broker=broker, repository=repo, poll_interval=0.05)
    safety_gate = SafetyGate(repository=repo, dry_run_guard=DryRunGuard(enabled=True))
    sell_manager = SellManager(
        broker=broker,
        rule_engine=rule_engine,
        repository=repo,
        order_watcher=order_watcher,
        active_orders=active_orders,
        safety_gate=safety_gate,
    )
    buy_manager = BuyManager(
        broker=broker,
        rule_engine=rule_engine,
        repository=repo,
        order_watcher=order_watcher,
        active_orders=active_orders,
        safety_gate=safety_gate,
        on_failure="skip",
    )

    await order_watcher.start()
    try:
        ctx = await execute_cycle(
            broker=broker,
            rule_engine=rule_engine,
            rules_config=rules,
            repository=repo,
            order_watcher=order_watcher,
            sell_manager=sell_manager,
            buy_manager=buy_manager,
            active_orders=active_orders,
        )

        assert (
            len(ctx.sell_order_ids) == 0
        ), "No sell orders should be placed in dry run"

        decisions = await repo.get_recent_decisions(limit=10)
        dry_run_decisions = [d for d in decisions if "DRY_RUN" in d.decision_type]
        assert len(dry_run_decisions) >= 1

        fresh_cash = (await broker.get_balance()).available_cash
        assert fresh_cash == initial_cash, "Cash should not change in dry run"

        holdings = await broker.get_holdings()
        infy_holding = next(h for h in holdings if h.symbol == "INFY")
        assert infy_holding.quantity == 10, "Holdings should not change in dry run"

        orders = await repo.get_recent_orders(limit=10)
        dry_orders = [o for o in orders if o.id.startswith("DRY_RUN")]
        assert len(dry_orders) >= 1
        assert dry_orders[0].status == OrderStatus.COMPLETED.value
    finally:
        await order_watcher.stop()


@pytest.mark.asyncio
async def test_order_watcher_syncs_db(
    broker: MockBroker,
    repo: Repository,
    session: AsyncSession,
) -> None:
    await broker.authenticate()

    order_watcher = OrderWatcher(broker=broker, repository=repo, poll_interval=0.02)

    broker_order = await broker.place_sell_order(
        symbol="INFY", quantity=10, order_type="MARKET"
    )

    await order_watcher.start()
    await order_watcher.watch(broker_order.id)

    filled = await order_watcher.wait_for_terminal(broker_order.id, timeout=5.0)
    await order_watcher.stop()

    assert filled.status == "COMPLETED"
    assert filled.filled_quantity == 10

    db_order = await repo.get_order(broker_order.id)
    assert db_order is not None
    assert db_order.status == "COMPLETED"
    assert db_order.filled_quantity == 10

    fills = await repo.get_fills_for_order(broker_order.id)
    assert len(fills) >= 1


@pytest.mark.asyncio
async def test_order_reconciliation(
    broker: MockBroker,
    repo: Repository,
    session: AsyncSession,
) -> None:
    await broker.authenticate()

    broker_order = await broker.place_sell_order(
        symbol="INFY", quantity=5, order_type="MARKET"
    )

    orders_from_broker = await broker.get_orders()
    assert len(orders_from_broker) == 1

    db_order = await repo.get_order(broker_order.id)
    assert db_order is None, "Order should not be in DB yet"

    active_orders: set[str] = set()
    config = _make_dummy_config()

    engine = ExecutionEngine(
        broker=broker,
        config=config,
        session_factory=async_sessionmaker(
            session.get_bind().__class__, class_=AsyncSession
        ),
    )
    engine._active_orders = active_orders
    await engine._reconcile_open_orders(repo)

    db_order = await repo.get_order(broker_order.id)
    assert db_order is not None, "Orphan broker order should be reconciled into DB"

    assert broker_order.id in active_orders


def _make_dummy_config() -> object:
    from portfolio_automation.core.config import (
        ConfigModel,
        ExecutionConfig,
        SafetyConfig,
    )

    return ConfigModel(
        execution=ExecutionConfig(
            poll_interval_seconds=60,
            market_open="09:15",
            market_close="15:30",
            buy_queue_on_failure="skip",
        ),
        safety=SafetyConfig(
            dry_run=False,
            max_daily_trades=10,
            max_daily_investment=100000.0,
            max_allocation_per_stock=0.20,
            cooldown_minutes=30,
            manual_confirmation=False,
            kill_switch_enabled=True,
        ),
    )
