from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from portfolio_automation.broker.interface import Broker
from portfolio_automation.broker.models import Holding
from portfolio_automation.core.exceptions import BrokerOrderRejectedError
from portfolio_automation.core.types import OrderStatus
from portfolio_automation.execution.order_watcher import OrderWatcher
from portfolio_automation.notifications.interface import EventType
from portfolio_automation.persistence.models import Order as DBOrder
from portfolio_automation.persistence.repository import Repository
from portfolio_automation.rules.context import RuleContext
from portfolio_automation.rules.engine import RuleEngine
from portfolio_automation.safety.gate import SafetyGate

logger = logging.getLogger(__name__)


class SellManager:
    def __init__(
        self,
        broker: Broker,
        rule_engine: RuleEngine,
        repository: Repository,
        order_watcher: OrderWatcher,
        active_orders: set[str],
        safety_gate: SafetyGate,
        notifier: Any | None = None,
    ) -> None:
        self._broker = broker
        self._rule_engine = rule_engine
        self._repo = repository
        self._order_watcher = order_watcher
        self._active_orders = active_orders
        self._safety_gate = safety_gate
        self._notifier = notifier

    async def evaluate_and_sell(
        self,
        holdings: list[Holding],
        prices: dict[str, float],
        portfolio_value: float,
        available_cash: float,
        cycle_id: str,
        candles: dict[str, list[dict[str, Any]]] | None = None,
    ) -> list[str]:
        placed_order_ids: list[str] = []

        for holding in holdings:
            context = RuleContext(
                symbol=holding.symbol,
                current_price=prices.get(holding.symbol, holding.current_price or 0.0),
                avg_buy_price=holding.avg_price,
                quantity=holding.quantity,
                portfolio_value=portfolio_value,
                available_cash=available_cash,
                historical_candles=candles.get(holding.symbol) if candles else None,
            )

            decisions = self._rule_engine.evaluate_sell_rules(context)

            triggered = [d for d in decisions if d.met]
            if not triggered:
                continue

            reason = triggered[0].reason

            if await self._is_duplicate(holding.symbol, "SELL"):
                logger.info(
                    "Sell already pending for %s, skipping",
                    holding.symbol,
                )
                await self._record_decision(
                    "SELL_SKIP_DUPLICATE", holding.symbol, reason, cycle_id
                )
                continue

            quantity = holding.quantity
            current_price = context.current_price

            safety = await self._safety_gate.can_sell(
                symbol=holding.symbol,
                quantity=quantity,
                price=current_price,
            )

            if not safety.passed:
                logger.info(
                    "Safety gate blocked sell for %s: %s",
                    holding.symbol,
                    safety.reason,
                )
                await self._record_decision(
                    "SELL_BLOCKED_SAFETY", holding.symbol, safety.reason, cycle_id
                )
                if self._notifier:
                    self._notifier.fire_and_forget(
                        EventType.SAFETY_TRIGGERED,
                        {
                            "symbol": holding.symbol,
                            "reason": safety.reason,
                            "side": "SELL",
                        },
                    )
                continue

            if safety.is_dry_run:
                logger.info(
                    "DRY RUN: Would sell %d shares of %s at %.2f — %s",
                    quantity,
                    holding.symbol,
                    current_price,
                    reason,
                )
                await self._record_decision(
                    "SELL_DRY_RUN", holding.symbol, reason, cycle_id
                )
                await self._save_dry_run_order(
                    holding.symbol, "SELL", quantity, current_price, cycle_id
                )
                if self._notifier:
                    self._notifier.fire_and_forget(
                        EventType.ORDER_PLACED,
                        {
                            "symbol": holding.symbol,
                            "quantity": quantity,
                            "price": current_price,
                            "side": "SELL",
                            "total": quantity * current_price,
                            "is_dry_run": True,
                        },
                    )
                continue

            try:
                broker_order = await self._broker.place_sell_order(
                    symbol=holding.symbol,
                    quantity=quantity,
                    order_type="MARKET",
                    price=current_price,
                )
            except BrokerOrderRejectedError as e:
                logger.warning("Sell order rejected for %s: %s", holding.symbol, e)
                await self._record_decision(
                    "SELL_REJECTED", holding.symbol, str(e), cycle_id
                )
                if self._notifier:
                    self._notifier.fire_and_forget(
                        EventType.ORDER_REJECTED,
                        {"symbol": holding.symbol, "reason": str(e), "side": "SELL"},
                    )
                continue

            self._active_orders.add(broker_order.id)

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
            await self._record_decision("SELL_PLACED", holding.symbol, reason, cycle_id)

            await self._order_watcher.watch(broker_order.id)
            await self._safety_gate.cooldown_manager.start_cooldown(
                holding.symbol, broker_order.id
            )
            placed_order_ids.append(broker_order.id)

            if self._notifier:
                self._notifier.fire_and_forget(
                    EventType.ORDER_PLACED,
                    {
                        "symbol": holding.symbol,
                        "quantity": quantity,
                        "price": current_price,
                        "order_id": broker_order.id,
                        "side": "SELL",
                        "total": quantity * current_price,
                    },
                )

            logger.info(
                "Sell order placed: %d shares of %s at %.2f (order=%s)",
                quantity,
                holding.symbol,
                current_price,
                broker_order.id,
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
