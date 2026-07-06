from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from portfolio_automation.core.types import OrderStatus
from portfolio_automation.persistence.models import (
    CashBalanceRecord,
    ConfigEntry,
    HoldingSnapshot,
    LogEntry,
    Order,
    OrderFill,
    StockCooldown,
    StrategyDecision,
)


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._lock = asyncio.Lock()
        for attr_name in dir(self):
            if not attr_name.startswith("_"):
                attr = getattr(self, attr_name)
                if inspect.iscoroutinefunction(attr):
                    setattr(self, attr_name, self._wrap_with_lock(attr))

    def _wrap_with_lock(self, fn: Any) -> Any:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async with self._lock:
                return await fn(*args, **kwargs)

        return wrapper

    # ── Holdings ──────────────────────────────────────────────────────

    async def save_holdings_snapshot(
        self,
        symbol: str,
        quantity: int,
        avg_price: float,
        current_price: float | None = None,
        cycle_id: str | None = None,
    ) -> HoldingSnapshot:
        record = HoldingSnapshot(
            symbol=symbol,
            quantity=quantity,
            avg_price=avg_price,
            current_price=current_price,
            value=(current_price * quantity) if current_price else None,
            cycle_id=cycle_id,
        )
        self._session.add(record)
        await self._session.commit()
        return record

    async def get_latest_holdings(
        self, cycle_id: str | None = None
    ) -> Sequence[HoldingSnapshot]:
        stmt = select(HoldingSnapshot)
        if cycle_id:
            stmt = stmt.where(HoldingSnapshot.cycle_id == cycle_id)
        stmt = stmt.order_by(HoldingSnapshot.timestamp.desc())
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ── Orders ─────────────────────────────────────────────────────────

    async def save_order(self, order: Order) -> Order:
        self._session.add(order)
        await self._session.commit()
        await self._session.refresh(order)
        return order

    async def update_order_status(
        self,
        order_id: str,
        status: OrderStatus | str,
        filled_quantity: int | None = None,
        filled_price: float | None = None,
        error_message: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        values: dict = {
            "status": str(status) if isinstance(status, OrderStatus) else status
        }
        if filled_quantity is not None:
            values["filled_quantity"] = filled_quantity
        if filled_price is not None:
            values["filled_price"] = filled_price
        if error_message is not None:
            values["error_message"] = error_message
        if completed_at is not None:
            values["completed_at"] = completed_at

        stmt = update(Order).where(Order.id == order_id).values(**values)
        await self._session.execute(stmt)
        await self._session.commit()

    async def get_order(self, order_id: str) -> Order | None:
        stmt = select(Order).where(Order.id == order_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_pending_orders(self, symbol: str | None = None) -> Sequence[Order]:
        stmt = select(Order).where(
            Order.status.in_(["PENDING", "OPEN", "PARTIALLY_FILLED"])
        )
        if symbol:
            stmt = stmt.where(Order.symbol == symbol)
        stmt = stmt.order_by(Order.placed_at.desc())
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_orders_by_symbol(
        self, symbol: str, limit: int = 50
    ) -> Sequence[Order]:
        stmt = (
            select(Order)
            .where(Order.symbol == symbol)
            .order_by(Order.placed_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_recent_orders(self, limit: int = 50) -> Sequence[Order]:
        stmt = select(Order).order_by(Order.placed_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ── Order Fills ────────────────────────────────────────────────────

    async def save_order_fill(
        self,
        order_id: str,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
    ) -> OrderFill:
        fill = OrderFill(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
        )
        self._session.add(fill)
        await self._session.commit()
        return fill

    async def get_fills_for_order(self, order_id: str) -> Sequence[OrderFill]:
        stmt = (
            select(OrderFill)
            .where(OrderFill.order_id == order_id)
            .order_by(OrderFill.executed_at.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ── Cash Balance ───────────────────────────────────────────────────

    async def record_cash_balance(
        self,
        available_cash: float,
        used_cash: float,
        total_cash: float,
    ) -> CashBalanceRecord:
        record = CashBalanceRecord(
            available_cash=available_cash,
            used_cash=used_cash,
            total_cash=total_cash,
        )
        self._session.add(record)
        await self._session.commit()
        return record

    async def get_cash_balance_history(
        self, limit: int = 100
    ) -> Sequence[CashBalanceRecord]:
        stmt = (
            select(CashBalanceRecord)
            .order_by(CashBalanceRecord.timestamp.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ── Strategy Decisions ─────────────────────────────────────────────

    async def record_decision(
        self,
        decision_type: str,
        symbol: str,
        reason: str,
        cycle_id: str | None = None,
        details: str | None = None,
    ) -> StrategyDecision:
        decision = StrategyDecision(
            decision_type=decision_type,
            symbol=symbol,
            reason=reason,
            cycle_id=cycle_id,
            details=details,
        )
        self._session.add(decision)
        await self._session.commit()
        return decision

    async def get_recent_decisions(
        self, limit: int = 100
    ) -> Sequence[StrategyDecision]:
        stmt = (
            select(StrategyDecision)
            .order_by(StrategyDecision.timestamp.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ── Logs ───────────────────────────────────────────────────────────

    async def save_log(
        self,
        level: str,
        module: str,
        message: str,
        cycle_id: str | None = None,
    ) -> LogEntry:
        entry = LogEntry(
            level=level,
            module=module,
            message=message,
            cycle_id=cycle_id,
        )
        self._session.add(entry)
        await self._session.commit()
        return entry

    async def get_recent_logs(self, limit: int = 100) -> Sequence[LogEntry]:
        stmt = select(LogEntry).order_by(LogEntry.timestamp.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ── Cooldowns ───────────────────────────────────────────────────────

    async def save_cooldown(
        self,
        symbol: str,
        sell_order_id: str,
        cooldown_until: datetime,
    ) -> StockCooldown:
        record = StockCooldown(
            symbol=symbol,
            sell_order_id=sell_order_id,
            cooldown_until=cooldown_until,
        )
        self._session.add(record)
        await self._session.commit()
        return record

    async def get_active_cooldown(self, symbol: str) -> StockCooldown | None:
        now = datetime.now(UTC)
        stmt = (
            select(StockCooldown)
            .where(StockCooldown.symbol == symbol)
            .where(StockCooldown.cooldown_until > now)
            .order_by(StockCooldown.cooldown_until.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_active_cooldowns(self) -> Sequence[StockCooldown]:
        now = datetime.now(UTC)
        stmt = (
            select(StockCooldown)
            .where(StockCooldown.cooldown_until > now)
            .order_by(StockCooldown.symbol)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ── Daily Limits ──────────────────────────────────────────────────

    async def get_daily_trade_count(self) -> int:
        today_start = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        stmt = select(Order).where(
            Order.status == "COMPLETED",
            Order.placed_at >= today_start,
        )
        result = await self._session.execute(stmt)
        return len(result.scalars().all())

    async def get_daily_buy_investment(self) -> float:
        today_start = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        stmt = select(Order).where(
            Order.side == "BUY",
            Order.status == "COMPLETED",
            Order.placed_at >= today_start,
        )
        result = await self._session.execute(stmt)
        orders = result.scalars().all()
        return sum((o.filled_quantity or 0) * (o.filled_price or 0.0) for o in orders)

    async def get_portfolio_value(self) -> float:
        stmt = (
            select(CashBalanceRecord)
            .order_by(CashBalanceRecord.timestamp.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.total_cash if record else 0.0

    # ── Config Store ───────────────────────────────────────────────────

    async def _get_config(self, key: str) -> ConfigEntry | None:
        stmt = select(ConfigEntry).where(ConfigEntry.key == key)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_config(self, key: str) -> ConfigEntry | None:
        return await self._get_config(key)

    async def set_config(self, key: str, value: str) -> ConfigEntry:
        existing = await self._get_config(key)
        if existing:
            stmt = (
                update(ConfigEntry)
                .where(ConfigEntry.key == key)
                .values(value=value, updated_at=datetime.now())
            )
            await self._session.execute(stmt)
        else:
            self._session.add(ConfigEntry(key=key, value=value))
        await self._session.commit()
        return await self._get_config(key)

    async def get_all_config(self) -> Sequence[ConfigEntry]:
        stmt = select(ConfigEntry).order_by(ConfigEntry.key)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def delete_config(self, key: str) -> None:
        stmt = delete(ConfigEntry).where(ConfigEntry.key == key)
        await self._session.execute(stmt)
        await self._session.commit()
