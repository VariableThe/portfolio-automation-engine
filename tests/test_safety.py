from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from portfolio_automation.persistence.models import Base, Order
from portfolio_automation.persistence.repository import Repository
from portfolio_automation.safety.circuit_breaker import KillSwitch
from portfolio_automation.safety.cooldown import StockCooldownManager
from portfolio_automation.safety.dry_run import DryRunGuard
from portfolio_automation.safety.gate import SafetyGate
from portfolio_automation.safety.limits import DailyLimits
from portfolio_automation.safety.manual_confirmation import (
    ManualConfirmation,
)


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


# ── Cooldown Persistence ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cooldown_persistence(repo: Repository) -> None:
    cooldown_mgr = StockCooldownManager(repository=repo, cooldown_hours=24)

    await cooldown_mgr.start_cooldown("INFY", "SELL_ORDER_001")

    reason = await cooldown_mgr.check_cooldown("INFY")
    assert reason is not None
    assert "Cooldown active for INFY" in reason

    new_cooldown_mgr = StockCooldownManager(repository=repo, cooldown_hours=24)
    reason = await new_cooldown_mgr.check_cooldown("INFY")
    assert reason is not None
    assert "Cooldown active for INFY" in reason

    all_active = await new_cooldown_mgr.get_all_active_cooldowns()
    assert len(all_active) == 1
    assert all_active[0]["symbol"] == "INFY"


@pytest.mark.asyncio
async def test_cooldown_expired(repo: Repository) -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    await repo.save_cooldown("INFY", "SELL_001", past)

    cooldown_mgr = StockCooldownManager(repository=repo, cooldown_hours=24)
    reason = await cooldown_mgr.check_cooldown("INFY")
    assert reason is None, "Expired cooldown should not block"


@pytest.mark.asyncio
async def test_cooldown_restart_simulation(
    repo: Repository, session: AsyncSession, engine: AsyncEngine
) -> None:
    cooldown_mgr = StockCooldownManager(repository=repo, cooldown_hours=24)
    await cooldown_mgr.start_cooldown("TCS", "SELL_TCS_001")

    new_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with new_maker() as new_session:
        new_repo = Repository(new_session)
        restarted_mgr = StockCooldownManager(repository=new_repo, cooldown_hours=24)

        reason = await restarted_mgr.check_cooldown("TCS")
        assert reason is not None, "Cooldown should survive restart"


# ── Daily Limits ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_daily_trade_limit(repo: Repository) -> None:
    safety_gate = SafetyGate(
        repository=repo,
        dry_run_guard=DryRunGuard(enabled=False),
        daily_limits=DailyLimits(
            repository=repo,
            max_daily_trades=2,
            max_daily_investment=1_000_000.0,
            max_allocation_per_stock=1.0,
        ),
    )

    result1 = await safety_gate.can_buy(
        symbol="INFY", quantity=1, price=100.0, portfolio_value=100_000.0
    )
    assert result1.passed, f"First buy should pass: {result1.reason}"

    await repo.save_order(
        Order(
            id="ORDER_001",
            symbol="INFY",
            side="SELL",
            order_type="MARKET",
            quantity=10,
            price=1500.0,
            status="COMPLETED",
            filled_quantity=10,
            filled_price=1500.0,
            placed_at=datetime.now(UTC),
        )
    )

    result2 = await safety_gate.can_buy(
        symbol="INFY", quantity=1, price=100.0, portfolio_value=100_000.0
    )
    assert result2.passed, f"Second buy should pass: {result2.reason}"

    await repo.save_order(
        Order(
            id="ORDER_002",
            symbol="TCS",
            side="SELL",
            order_type="MARKET",
            quantity=5,
            price=3500.0,
            status="COMPLETED",
            filled_quantity=5,
            filled_price=3500.0,
            placed_at=datetime.now(UTC),
        )
    )

    result3 = await safety_gate.can_buy(
        symbol="INFY", quantity=1, price=100.0, portfolio_value=100_000.0
    )
    assert not result3.passed, "Third buy should be blocked by daily trade limit"
    assert "Daily trade limit" in result3.reason


@pytest.mark.asyncio
async def test_daily_investment_limit(repo: Repository) -> None:
    safety_gate = SafetyGate(
        repository=repo,
        dry_run_guard=DryRunGuard(enabled=False),
        daily_limits=DailyLimits(
            repository=repo,
            max_daily_trades=100,
            max_daily_investment=10_000.0,
            max_allocation_per_stock=1.0,
        ),
    )

    result1 = await safety_gate.can_buy(
        symbol="INFY", quantity=1, price=5_000.0, portfolio_value=100_000.0
    )
    assert result1.passed, f"Buy within limit should pass: {result1.reason}"

    await repo.save_order(
        Order(
            id="ORDER_BUY_001",
            symbol="INFY",
            side="BUY",
            order_type="MARKET",
            quantity=1,
            price=5000.0,
            status="COMPLETED",
            filled_quantity=1,
            filled_price=5000.0,
            placed_at=datetime.now(UTC),
        )
    )

    result2 = await safety_gate.can_buy(
        symbol="TCS", quantity=1, price=6_000.0, portfolio_value=100_000.0
    )
    assert not result2.passed, "Buy exceeding daily investment should be blocked"
    assert "Daily investment limit" in result2.reason


# ── Kill Switch ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kill_switch_blocks_orders(repo: Repository) -> None:
    kill_switch = KillSwitch()
    gate = SafetyGate(
        repository=repo,
        kill_switch=kill_switch,
        dry_run_guard=DryRunGuard(enabled=False),
    )

    result = await gate.can_buy(
        symbol="INFY", quantity=1, price=100.0, portfolio_value=100_000.0
    )
    assert result.passed, "Buy should pass before kill switch activated"

    kill_switch.activate()
    assert kill_switch.is_active()

    result = await gate.can_buy(
        symbol="INFY", quantity=1, price=100.0, portfolio_value=100_000.0
    )
    assert not result.passed, "Buy should be blocked by kill switch"
    assert "Kill switch" in result.reason

    result = await gate.can_sell(symbol="INFY", quantity=1, price=100.0)
    assert not result.passed, "Sell should also be blocked by kill switch"
    assert "Kill switch" in result.reason

    kill_switch.deactivate()
    assert not kill_switch.is_active()

    result = await gate.can_buy(
        symbol="INFY", quantity=1, price=100.0, portfolio_value=100_000.0
    )
    assert result.passed, "Buy should pass after kill switch deactivated"


@pytest.mark.asyncio
async def test_kill_switch_async_event(repo: Repository) -> None:
    kill_switch = KillSwitch()
    gate = SafetyGate(
        repository=repo,
        kill_switch=kill_switch,
        dry_run_guard=DryRunGuard(enabled=False),
    )

    kill_switch.activate()
    results: list[bool] = []

    async def attempt_buy() -> None:
        result = await gate.can_buy(
            symbol="INFY", quantity=1, price=100.0, portfolio_value=100_000.0
        )
        results.append(result.passed)

    task = asyncio.create_task(attempt_buy())

    await asyncio.sleep(0.01)
    assert len(results) == 1
    assert results[0] is False

    await task


# ── Manual Confirmation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manual_confirmation_symbol(repo: Repository) -> None:
    manual = ManualConfirmation()
    gate = SafetyGate(
        repository=repo,
        dry_run_guard=DryRunGuard(enabled=False),
        manual_confirmation=manual,
    )

    assert not manual.is_flagged("INFY")
    result = await gate.can_buy(
        symbol="INFY", quantity=1, price=100.0, portfolio_value=100_000.0
    )
    assert result.passed, "Buy should pass without manual confirmation"

    manual.flag_symbol("INFY")
    assert manual.is_flagged("INFY")

    async def delayed_approve() -> None:
        await asyncio.sleep(0.05)
        manual.approve("INFY")

    approve_task = asyncio.create_task(delayed_approve())
    result = await gate.can_buy(
        symbol="INFY", quantity=1, price=100.0, portfolio_value=100_000.0
    )
    await approve_task
    assert result.passed, "Buy should pass after manual approval"


@pytest.mark.asyncio
async def test_manual_confirmation_unflag(repo: Repository) -> None:
    manual = ManualConfirmation()
    gate = SafetyGate(
        repository=repo,
        dry_run_guard=DryRunGuard(enabled=False),
        manual_confirmation=manual,
    )

    manual.flag_symbol("INFY")
    assert manual.is_flagged("INFY")

    manual.unflag_symbol("INFY")
    assert not manual.is_flagged("INFY")

    result = await gate.can_buy(
        symbol="INFY", quantity=1, price=100.0, portfolio_value=100_000.0
    )
    assert result.passed


# ── Dry Run ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_gate_block(repo: Repository) -> None:
    gate = SafetyGate(
        repository=repo,
        dry_run_guard=DryRunGuard(enabled=True),
    )

    result = await gate.can_buy(
        symbol="INFY", quantity=10, price=1500.0, portfolio_value=100_000.0
    )
    assert result.passed, "Dry run should pass (returns passed=True)"
    assert result.is_dry_run, "Should be marked as dry run"
    assert "Dry-run" in result.reason

    result = await gate.can_sell(symbol="INFY", quantity=10, price=1500.0)
    assert result.passed, "Dry run sell should also pass"
    assert result.is_dry_run
    assert "Dry-run" in result.reason
