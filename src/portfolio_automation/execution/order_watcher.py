from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from portfolio_automation.broker.interface import Broker
from portfolio_automation.broker.models import Order as BrokerOrder
from portfolio_automation.notifications.interface import EventType
from portfolio_automation.persistence.models import Order as DBOrder
from portfolio_automation.persistence.repository import Repository

logger = logging.getLogger(__name__)


class OrderWatcher:
    def __init__(
        self,
        broker: Broker,
        repository: Repository,
        poll_interval: float = 1.0,
        notifier: Any | None = None,
    ) -> None:
        self._broker = broker
        self._repo = repository
        self._poll_interval = poll_interval
        self._notifier = notifier
        self._watched: dict[str, BrokerOrder | None] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    @property
    def active_order_ids(self) -> set[str]:
        return set(self._watched.keys())

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await asyncio.wait_for(self._task, timeout=5.0)

    async def watch(self, order_id: str) -> None:
        if order_id not in self._watched:
            self._watched[order_id] = None
            self._events[order_id] = asyncio.Event()

    async def wait_for_terminal(
        self, order_id: str, timeout: float = 60.0
    ) -> BrokerOrder:
        event = self._events.get(order_id)
        if event is None:
            raise ValueError(f"Order {order_id} is not being watched")

        await asyncio.wait_for(event.wait(), timeout=timeout)
        order = self._watched.get(order_id)
        if order is None:
            raise RuntimeError(f"Order {order_id} not found after terminal event")
        return order

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            for order_id in list(self._watched.keys()):
                try:
                    broker_order = await self._broker.get_order_status(order_id)
                    self._watched[order_id] = broker_order
                    await self._sync_to_db(broker_order)

                    if broker_order.status in ("COMPLETED", "REJECTED", "CANCELLED"):
                        event = self._events.get(order_id)
                        if event:
                            event.set()
                except Exception:
                    logger.exception("Failed to poll order %s", order_id)

            await asyncio.sleep(self._poll_interval)

    async def _sync_to_db(self, broker_order: BrokerOrder) -> None:
        existing = await self._repo.get_order(broker_order.id)

        if existing is None:
            db_order = DBOrder(
                id=broker_order.id,
                symbol=broker_order.symbol,
                side=broker_order.side,
                order_type=broker_order.order_type,
                quantity=broker_order.quantity,
                price=broker_order.price,
                status=broker_order.status,
                filled_quantity=broker_order.filled_quantity,
                filled_price=broker_order.filled_price,
                placed_at=broker_order.placed_at or datetime.now(UTC),
                completed_at=broker_order.completed_at,
                error_message=broker_order.error_message,
            )
            await self._repo.save_order(db_order)
            if broker_order.filled_quantity > 0:
                await self._repo.save_order_fill(
                    order_id=broker_order.id,
                    symbol=broker_order.symbol,
                    side=broker_order.side,
                    quantity=broker_order.filled_quantity,
                    price=broker_order.filled_price or broker_order.price or 0.0,
                )
            if self._notifier and broker_order.status == "COMPLETED":
                self._notifier.fire_and_forget(
                    EventType.ORDER_FILLED,
                    {
                        "symbol": broker_order.symbol,
                        "quantity": broker_order.filled_quantity
                        or broker_order.quantity,
                        "price": broker_order.filled_price or broker_order.price,
                        "order_id": broker_order.id,
                        "side": broker_order.side,
                        "total": (
                            broker_order.filled_price or broker_order.price or 0.0
                        )
                        * (broker_order.filled_quantity or broker_order.quantity),
                    },
                )
            elif self._notifier and broker_order.status == "REJECTED":
                self._notifier.fire_and_forget(
                    EventType.ORDER_REJECTED,
                    {
                        "symbol": broker_order.symbol,
                        "side": broker_order.side,
                        "order_id": broker_order.id,
                        "reason": broker_order.error_message or "Order rejected",
                    },
                )
            return

        if existing.status == broker_order.status and (
            broker_order.status not in ("OPEN", "PARTIALLY_FILLED")
            or existing.filled_quantity == broker_order.filled_quantity
        ):
            return

        new_fills = broker_order.filled_quantity - existing.filled_quantity
        old_status = existing.status
        await self._repo.update_order_status(
            order_id=broker_order.id,
            status=broker_order.status,
            filled_quantity=broker_order.filled_quantity,
            filled_price=broker_order.filled_price,
            error_message=broker_order.error_message,
            completed_at=broker_order.completed_at,
        )

        if new_fills > 0:
            await self._repo.save_order_fill(
                order_id=broker_order.id,
                symbol=broker_order.symbol,
                side=broker_order.side,
                quantity=new_fills,
                price=broker_order.filled_price or 0.0,
            )

        if self._notifier:
            if broker_order.status == "COMPLETED" and old_status != "COMPLETED":
                self._notifier.fire_and_forget(
                    EventType.ORDER_FILLED,
                    {
                        "symbol": broker_order.symbol,
                        "quantity": broker_order.filled_quantity
                        or broker_order.quantity,
                        "price": broker_order.filled_price or broker_order.price,
                        "order_id": broker_order.id,
                        "side": broker_order.side,
                        "total": (
                            broker_order.filled_price or broker_order.price or 0.0
                        )
                        * (broker_order.filled_quantity or broker_order.quantity),
                    },
                )
            elif broker_order.status == "REJECTED" and old_status != "REJECTED":
                self._notifier.fire_and_forget(
                    EventType.ORDER_REJECTED,
                    {
                        "symbol": broker_order.symbol,
                        "side": broker_order.side,
                        "order_id": broker_order.id,
                        "reason": broker_order.error_message or "Order rejected",
                    },
                )
