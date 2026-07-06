from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from portfolio_automation.persistence.models import (
    Base,
    HoldingSnapshot,
    Order,
)
from portfolio_automation.persistence.repository import Repository


@pytest.fixture(name="engine")
async def engine_fixture() -> AsyncEngine:
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture(name="session")
async def session_fixture(engine: AsyncEngine) -> AsyncSession:
    session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_maker() as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(session: AsyncSession) -> Repository:
    return Repository(session)


@pytest.mark.asyncio
async def test_create_holdings_snapshot(repo: Repository) -> None:
    record = await repo.save_holdings_snapshot(
        symbol="INFY",
        quantity=10,
        avg_price=1500.0,
        current_price=1600.0,
        cycle_id="cycle_001",
    )
    assert record.id is not None
    assert record.symbol == "INFY"
    assert record.quantity == 10
    assert record.avg_price == 1500.0
    assert record.current_price == 1600.0
    assert record.value == 16000.0
    assert record.cycle_id == "cycle_001"


@pytest.mark.asyncio
async def test_get_latest_holdings(repo: Repository) -> None:
    await repo.save_holdings_snapshot(symbol="TCS", quantity=5, avg_price=3500.0)
    await repo.save_holdings_snapshot(
        symbol="TCS", quantity=5, avg_price=3500.0, cycle_id="c2"
    )
    results = await repo.get_latest_holdings()
    assert len(results) >= 2


@pytest.mark.asyncio
async def test_save_and_get_order(repo: Repository) -> None:
    order = Order(
        id="order_001",
        symbol="HAL",
        side="BUY",
        order_type="MARKET",
        quantity=10,
        price=None,
        status="PENDING",
    )
    saved = await repo.save_order(order)
    assert saved.id == "order_001"

    fetched = await repo.get_order("order_001")
    assert fetched is not None
    assert fetched.symbol == "HAL"
    assert fetched.side == "BUY"


@pytest.mark.asyncio
async def test_update_order_status(repo: Repository) -> None:
    order = Order(
        id="order_002",
        symbol="BEL",
        side="SELL",
        order_type="LIMIT",
        quantity=5,
        price=500.0,
        status="PENDING",
    )
    await repo.save_order(order)

    await repo.update_order_status(
        order_id="order_002",
        status="COMPLETED",
        filled_quantity=5,
        filled_price=500.0,
    )

    updated = await repo.get_order("order_002")
    assert updated is not None
    assert updated.status == "COMPLETED"
    assert updated.filled_quantity == 5
    assert updated.filled_price == 500.0


@pytest.mark.asyncio
async def test_get_pending_orders(repo: Repository) -> None:
    o1 = Order(
        id="o_p1",
        symbol="RELIANCE",
        side="BUY",
        order_type="MARKET",
        quantity=1,
        status="PENDING",
    )
    o2 = Order(
        id="o_p2",
        symbol="TCS",
        side="SELL",
        order_type="MARKET",
        quantity=1,
        status="OPEN",
    )
    o3 = Order(
        id="o_c1",
        symbol="INFY",
        side="BUY",
        order_type="MARKET",
        quantity=1,
        status="COMPLETED",
    )
    for o in (o1, o2, o3):
        await repo.save_order(o)

    pending = await repo.get_pending_orders()
    order_ids = [o.id for o in pending]
    assert "o_p1" in order_ids
    assert "o_p2" in order_ids
    assert "o_c1" not in order_ids


@pytest.mark.asyncio
async def test_get_pending_orders_by_symbol(repo: Repository) -> None:
    o1 = Order(
        id="o_ps1",
        symbol="HAL",
        side="BUY",
        order_type="MARKET",
        quantity=1,
        status="PENDING",
    )
    o2 = Order(
        id="o_ps2",
        symbol="BEL",
        side="BUY",
        order_type="MARKET",
        quantity=1,
        status="PENDING",
    )
    await repo.save_order(o1)
    await repo.save_order(o2)

    hal_orders = await repo.get_pending_orders(symbol="HAL")
    assert len(hal_orders) == 1
    assert hal_orders[0].id == "o_ps1"


@pytest.mark.asyncio
async def test_order_fills(repo: Repository) -> None:
    order = Order(
        id="fill_o1",
        symbol="HAL",
        side="BUY",
        order_type="MARKET",
        quantity=10,
        status="PARTIALLY_FILLED",
    )
    await repo.save_order(order)

    fill1 = await repo.save_order_fill(
        order_id="fill_o1", symbol="HAL", side="BUY", quantity=5, price=5200.0
    )
    fill2 = await repo.save_order_fill(
        order_id="fill_o1", symbol="HAL", side="BUY", quantity=5, price=5210.0
    )
    assert fill1.id is not None
    assert fill2.id is not None

    fills = await repo.get_fills_for_order("fill_o1")
    assert len(fills) == 2
    assert fills[0].quantity == 5


@pytest.mark.asyncio
async def test_cash_balance_history(repo: Repository) -> None:
    await repo.record_cash_balance(
        available_cash=50000.0, used_cash=5000.0, total_cash=55000.0
    )
    await repo.record_cash_balance(
        available_cash=45000.0, used_cash=10000.0, total_cash=55000.0
    )

    history = await repo.get_cash_balance_history()
    assert len(history) == 2
    assert history[0].available_cash == 45000.0


@pytest.mark.asyncio
async def test_strategy_decision_with_reason(repo: Repository) -> None:
    decision = await repo.record_decision(
        decision_type="SKIP",
        symbol="BEL",
        reason="Skipped BEL because available cash was ₹4,200 while required ₹5,100.",
        cycle_id="cycle_001",
        details='{"available_cash": 4200, "required_cash": 5100}',
    )
    assert decision.id is not None
    assert (
        decision.reason
        == "Skipped BEL because available cash was ₹4,200 while required ₹5,100."
    )
    assert decision.decision_type == "SKIP"

    recent = await repo.get_recent_decisions()
    assert len(recent) >= 1


@pytest.mark.asyncio
async def test_logs(repo: Repository) -> None:
    await repo.save_log(
        level="INFO", module="test", message="Test log entry", cycle_id="c1"
    )
    await repo.save_log(
        level="ERROR", module="test", message="Test error entry", cycle_id="c1"
    )

    logs = await repo.get_recent_logs()
    assert len(logs) == 2
    assert logs[0].level == "ERROR"


@pytest.mark.asyncio
async def test_config_store(repo: Repository) -> None:
    await repo.set_config("dry_run", "true")
    await repo.set_config("max_trades", "10")

    entry = await repo.get_config("dry_run")
    assert entry is not None
    assert entry.value == "true"

    await repo.set_config("dry_run", "false")
    entry = await repo.get_config("dry_run")
    assert entry.value == "false"

    all_configs = await repo.get_all_config()
    assert len(all_configs) == 2

    await repo.delete_config("max_trades")
    assert await repo.get_config("max_trades") is None


@pytest.mark.asyncio
async def test_rollback_on_error(repo: Repository, session: AsyncSession) -> None:
    await repo.save_holdings_snapshot(symbol="SAFE", quantity=1, avg_price=100.0)

    session.add(
        Order(
            id="rback1",
            symbol="HAL",
            side="BUY",
            order_type="MARKET",
            quantity=10,
            status="PENDING",
        )
    )
    session.add(
        Order(
            id="rback1",
            symbol="HAL",
            side="BUY",
            order_type="MARKET",
            quantity=10,
            status="PENDING",
        )
    )
    try:
        await session.commit()
    except Exception:
        await session.rollback()

    result = await session.execute(
        select(HoldingSnapshot).where(HoldingSnapshot.symbol == "SAFE")
    )
    safe = result.scalar_one_or_none()
    assert safe is not None
    assert safe.quantity == 1
