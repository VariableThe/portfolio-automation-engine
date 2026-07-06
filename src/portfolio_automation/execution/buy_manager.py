from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from portfolio_automation.broker.interface import Broker
from portfolio_automation.broker.models import Balance, Holding
from portfolio_automation.core.exceptions import BrokerOrderRejectedError
from portfolio_automation.core.types import OnFailureBehavior, OrderStatus
from portfolio_automation.execution.order_watcher import OrderWatcher
from portfolio_automation.notifications.interface import EventType
from portfolio_automation.persistence.models import Order as DBOrder
from portfolio_automation.persistence.repository import Repository
from portfolio_automation.rules.context import RuleContext
from portfolio_automation.rules.engine import RuleEngine
from portfolio_automation.rules.models import BuyQueueEntry
from portfolio_automation.safety.gate import SafetyGate

logger = logging.getLogger(__name__)


class BuyManager:
    def __init__(
        self,
        broker: Broker,
        rule_engine: RuleEngine,
        repository: Repository,
        order_watcher: OrderWatcher,
        active_orders: set[str],
        safety_gate: SafetyGate,
        on_failure: str = "skip",
        notifier: Any | None = None,
    ) -> None:
        self._broker = broker
        self._rule_engine = rule_engine
        self._repo = repository
        self._order_watcher = order_watcher
        self._active_orders = active_orders
        self._safety_gate = safety_gate
        self._on_failure = OnFailureBehavior(on_failure)
        self._notifier = notifier

    async def process_buy_queue(
        self,
        buy_queue: list[BuyQueueEntry],
        balance: Balance,
        prices: dict[str, float],
        holdings: list[Holding],
        cycle_id: str,
        candle_data: dict[str, list[dict[str, Any]]] | None = None,
    ) -> list[str]:
        placed_order_ids: list[str] = []
        remaining_cash = balance.available_cash
        sorted_queue = sorted(buy_queue, key=lambda e: e.priority)

        for entry in sorted_queue:
            if remaining_cash <= 0:
                logger.info("No cash remaining, stopping buy queue")
                break

            current_price = prices.get(entry.symbol)
            if current_price is None:
                logger.warning("Unknown price for %s, skipping", entry.symbol)
                await self._record_decision(
                    "BUY_SKIP_NO_PRICE", entry.symbol, "No price available", cycle_id
                )
                if self._on_failure == OnFailureBehavior.STOP:
                    break
                continue

            max_shares = int(remaining_cash // current_price)
            if max_shares <= 0:
                logger.info(
                    "Insufficient cash for %s (need %.2f, have %.2f)",
                    entry.symbol,
                    current_price,
                    remaining_cash,
                )
                await self._record_decision(
                    "BUY_SKIP_CASH",
                    entry.symbol,
                    f"Insufficient cash: need {current_price:.2f}, have {remaining_cash:.2f}",
                    cycle_id,
                )
                if self._on_failure == OnFailureBehavior.STOP:
                    break
                continue

            current_holding = next(
                (h for h in holdings if h.symbol == entry.symbol), None
            )

            context = RuleContext(
                symbol=entry.symbol,
                current_price=current_price,
                avg_buy_price=current_holding.avg_price if current_holding else None,
                quantity=current_holding.quantity if current_holding else 0,
                portfolio_value=balance.total_cash,
                available_cash=remaining_cash,
                historical_candles=(
                    candle_data.get(entry.symbol) if candle_data else None
                ),
            )

            decision = self._rule_engine.evaluate_buy_condition(entry, context)
            if not decision.met:
                logger.info(
                    "Buy condition not met for %s: %s",
                    entry.symbol,
                    decision.reason,
                )
                await self._record_decision(
                    "BUY_SKIP_CONDITION",
                    entry.symbol,
                    decision.reason,
                    cycle_id,
                )
                if self._on_failure == OnFailureBehavior.STOP:
                    break
                continue

            if await self._is_duplicate(entry.symbol, "BUY"):
                logger.info("Buy already pending for %s, skipping", entry.symbol)
                await self._record_decision(
                    "BUY_SKIP_DUPLICATE",
                    entry.symbol,
                    "Duplicate buy order prevented",
                    cycle_id,
                )
                if self._on_failure == OnFailureBehavior.STOP:
                    break
                continue

            quantity = max_shares
            safety = await self._safety_gate.can_buy(
                symbol=entry.symbol,
                quantity=quantity,
                price=current_price,
                portfolio_value=balance.total_cash,
            )

            if not safety.passed:
                logger.info(
                    "Safety gate blocked buy for %s: %s",
                    entry.symbol,
                    safety.reason,
                )
                await self._record_decision(
                    "BUY_BLOCKED_SAFETY",
                    entry.symbol,
                    safety.reason,
                    cycle_id,
                )
                if self._notifier:
                    self._notifier.fire_and_forget(
                        EventType.SAFETY_TRIGGERED,
                        {
                            "symbol": entry.symbol,
                            "reason": safety.reason,
                            "side": "BUY",
                        },
                    )
                if self._on_failure == OnFailureBehavior.STOP:
                    break
                continue

            if safety.is_dry_run:
                logger.info(
                    "DRY RUN: Would buy %d shares of %s at %.2f",
                    quantity,
                    entry.symbol,
                    current_price,
                )
                await self._record_decision(
                    "BUY_DRY_RUN",
                    entry.symbol,
                    f"Would buy {quantity} shares at {current_price:.2f}",
                    cycle_id,
                )
                await self._save_dry_run_order(
                    entry.symbol, "BUY", quantity, current_price, cycle_id
                )
                if self._notifier:
                    self._notifier.fire_and_forget(
                        EventType.ORDER_PLACED,
                        {
                            "symbol": entry.symbol,
                            "quantity": quantity,
                            "price": current_price,
                            "side": "BUY",
                            "total": quantity * current_price,
                            "is_dry_run": True,
                        },
                    )
                continue

            try:
                broker_order = await self._broker.place_buy_order(
                    symbol=entry.symbol,
                    quantity=quantity,
                    order_type="MARKET",
                    price=current_price,
                )
            except BrokerOrderRejectedError as e:
                logger.warning("Buy order rejected for %s: %s", entry.symbol, e)
                await self._record_decision(
                    "BUY_REJECTED", entry.symbol, str(e), cycle_id
                )
                if self._notifier:
                    self._notifier.fire_and_forget(
                        EventType.ORDER_REJECTED,
                        {"symbol": entry.symbol, "reason": str(e), "side": "BUY"},
                    )
                if self._on_failure == OnFailureBehavior.STOP:
                    break
                continue

            self._active_orders.add(broker_order.id)
            await self._order_watcher.watch(broker_order.id)

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
                placed_at=broker_order.placed_at,
                completed_at=broker_order.completed_at,
                error_message=broker_order.error_message,
                cycle_id=cycle_id,
            )
            await self._repo.save_order(db_order)
            await self._record_decision(
                "BUY_PLACED",
                entry.symbol,
                f"Buy {quantity} shares at {current_price:.2f}",
                cycle_id,
            )

            placed_order_ids.append(broker_order.id)

            if self._notifier:
                self._notifier.fire_and_forget(
                    EventType.ORDER_PLACED,
                    {
                        "symbol": entry.symbol,
                        "quantity": quantity,
                        "price": current_price,
                        "order_id": broker_order.id,
                        "side": "BUY",
                        "total": quantity * current_price,
                    },
                )

            cost = quantity * current_price
            remaining_cash -= cost

            try:
                fresh_balance = await self._broker.get_balance()
                remaining_cash = fresh_balance.available_cash
            except Exception:
                logger.warning("Could not refresh balance after buy, using estimate")

            logger.info(
                "Buy order placed: %d shares of %s at %.2f (order=%s, remaining=%.2f)",
                quantity,
                entry.symbol,
                current_price,
                broker_order.id,
                remaining_cash,
            )

        return placed_order_ids

    async def _is_duplicate(self, symbol: str, side: str) -> bool:
        pending = await self._repo.get_pending_orders(symbol)
        for order in pending:
            if order.side == side:
                return True
        return False

    async def _record_decision(
        self, decision_type: str, symbol: str, reason: str, cycle_id: str
    ) -> None:
        await self._repo.record_decision(
            decision_type=decision_type,
            symbol=symbol,
            reason=reason,
            cycle_id=cycle_id,
        )

    async def _save_dry_run_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        cycle_id: str,
    ) -> None:
        order_id = f"DRY_RUN_{datetime.now(UTC).strftime('%y%m%d%H%M%S%f')}"
        dry_order = DBOrder(
            id=order_id,
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=quantity,
            price=price,
            status=OrderStatus.COMPLETED.value,
            filled_quantity=quantity,
            filled_price=price,
            placed_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            cycle_id=cycle_id,
        )
        await self._repo.save_order(dry_order)
