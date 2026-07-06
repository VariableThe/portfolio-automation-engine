from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class HoldingSnapshot(Base):
    __tablename__ = "holdings_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=func.now(), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    cycle_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", index=True
    )
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    filled_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cycle_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)


class OrderFill(Base):
    __tablename__ = "order_fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=func.now(), nullable=False
    )


class CashBalanceRecord(Base):
    __tablename__ = "cash_balance_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=func.now(), nullable=False, index=True
    )
    available_cash: Mapped[float] = mapped_column(Float, nullable=False)
    used_cash: Mapped[float] = mapped_column(Float, nullable=False)
    total_cash: Mapped[float] = mapped_column(Float, nullable=False)


class StrategyDecision(Base):
    __tablename__ = "strategy_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=func.now(), nullable=False, index=True
    )
    cycle_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    decision_type: Mapped[str] = mapped_column(String(10), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)


class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=func.now(), nullable=False, index=True
    )
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    module: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    cycle_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)


class StockCooldown(Base):
    __tablename__ = "stock_cooldowns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    sell_order_id: Mapped[str] = mapped_column(String(50), nullable=False)
    cooldown_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=func.now(), nullable=False
    )


class ConfigEntry(Base):
    __tablename__ = "config_store"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
