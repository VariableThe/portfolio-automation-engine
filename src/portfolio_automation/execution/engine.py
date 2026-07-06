from __future__ import annotations

import asyncio
import logging
import signal
from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from portfolio_automation.broker.interface import Broker
from portfolio_automation.core.config import ConfigModel
from portfolio_automation.execution.buy_manager import BuyManager
from portfolio_automation.execution.cycle import execute_cycle
from portfolio_automation.execution.order_watcher import OrderWatcher
from portfolio_automation.execution.sell_manager import SellManager
from portfolio_automation.notifications.interface import EventType
from portfolio_automation.notifications.manager import NotificationManager
from portfolio_automation.persistence.models import Order as DBOrder
from portfolio_automation.persistence.repository import Repository
from portfolio_automation.rules.engine import RuleEngine
from portfolio_automation.rules.loader import load_rules
from portfolio_automation.rules.models import RulesConfig
from portfolio_automation.safety.cooldown import StockCooldownManager
from portfolio_automation.safety.dry_run import DryRunGuard
from portfolio_automation.safety.gate import SafetyGate
from portfolio_automation.safety.limits import DailyLimits

logger = logging.getLogger(__name__)


class ExecutionEngine:
    def __init__(
        self,
        broker: Broker,
        config: ConfigModel,
        session_factory: async_sessionmaker,
        rules_config: RulesConfig | None = None,
        candle_data: dict[str, list[dict[str, Any]]] | None = None,
        safety_gate: SafetyGate | None = None,
        notifier: NotificationManager | None = None,
        broadcaster: Any | None = None,
    ) -> None:
        self._broker = broker
        self._config = config
        self._session_factory = session_factory
        self._rules_config = rules_config
        self._candle_data = candle_data
        self._safety_gate = safety_gate
        self._notifier = notifier or NotificationManager.from_config(
            config.notifications
        )
        self._broadcaster = broadcaster
        if broadcaster and self._notifier:
            from portfolio_automation.dashboard.broadcaster import (
                DashboardNotificationChannel,
            )

            self._notifier.register_channel(
                "dashboard", DashboardNotificationChannel(broadcaster)
            )

        self._active_orders: set[str] = set()
        self._shutdown_requested: bool = False
        self._cycle_count: int = 0

    def broadcast_event(self, event_type: str, data: dict[str, Any]) -> None:
        if self._broadcaster:
            self._broadcaster.broadcast(event_type, data)

    async def setup(self) -> None:
        self._session = self._session_factory()
        self._repo = Repository(self._session)
        rules_config = self._rules_config or load_rules("config/rules.yaml")
        self._rule_engine = RuleEngine(rules_config)

        self._safety_gate = self._safety_gate or SafetyGate(
            repository=self._repo,
            dry_run_guard=DryRunGuard(enabled=self._config.safety.dry_run),
            daily_limits=DailyLimits(
                repository=self._repo,
                max_daily_trades=self._config.safety.max_daily_trades,
                max_daily_investment=self._config.safety.max_daily_investment,
                max_allocation_per_stock=self._config.safety.max_allocation_per_stock,
            ),
            cooldown_manager=StockCooldownManager(
                repository=self._repo,
                cooldown_hours=self._config.safety.cooldown_minutes / 60.0,
            ),
        )

        self._order_watcher = OrderWatcher(
            broker=self._broker,
            repository=self._repo,
            notifier=self._notifier,
            poll_interval=0.05,
        )

        self._sell_manager = SellManager(
            broker=self._broker,
            rule_engine=self._rule_engine,
            repository=self._repo,
            order_watcher=self._order_watcher,
            active_orders=self._active_orders,
            safety_gate=self._safety_gate,
            notifier=self._notifier,
        )

        self._buy_manager = BuyManager(
            broker=self._broker,
            rule_engine=self._rule_engine,
            repository=self._repo,
            order_watcher=self._order_watcher,
            active_orders=self._active_orders,
            safety_gate=self._safety_gate,
            on_failure=self._config.execution.buy_queue_on_failure,
            notifier=self._notifier,
        )

        await self._authenticate_broker()
        await self._reconcile_open_orders(self._repo)
        await self._order_watcher.start()

    async def step(self) -> Any | None:
        if not await self._is_market_open():
            logger.info("Market is closed, waiting for next poll")
            return None

        rules_config = self._rules_config or load_rules("config/rules.yaml")
        if self._rule_engine:
            self._rule_engine._rules = rules_config
        ctx = await execute_cycle(
            broker=self._broker,
            rule_engine=self._rule_engine,
            rules_config=rules_config,
            repository=self._repo,
            order_watcher=self._order_watcher,
            sell_manager=self._sell_manager,
            buy_manager=self._buy_manager,
            active_orders=self._active_orders,
            candle_data=self._candle_data,
        )

        self._cycle_count += 1
        return ctx

    async def stop(self) -> None:
        order_watcher = getattr(self, "_order_watcher", None)
        session = getattr(self, "_session", None)
        if order_watcher and session:
            await self._cleanup(order_watcher, session)

    async def run(self) -> None:
        await self.setup()
        try:
            try:
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(
                        sig, lambda: asyncio.create_task(self._shutdown())
                    )
            except (NotImplementedError, RuntimeError, ValueError):
                pass

            logger.info(
                "Execution engine started (dry_run=%s, poll=%ds)",
                self._config.safety.dry_run,
                self._config.execution.poll_interval_seconds,
            )

            while not self._shutdown_requested:
                try:
                    res = await self.step()
                    if res is None and not self._shutdown_requested:
                        await asyncio.sleep(
                            self._config.execution.poll_interval_seconds
                        )
                        continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.exception("Error in execution cycle")
                    if self._notifier:
                        self._notifier.fire_and_forget(
                            EventType.ERROR,
                            {
                                "reason": str(e),
                                "message": "Unhandled error in execution cycle",
                            },
                        )

                if not self._shutdown_requested:
                    await asyncio.sleep(self._config.execution.poll_interval_seconds)

        finally:
            await self.stop()

    async def _authenticate_broker(self) -> None:
        try:
            await self._broker.authenticate()
            logger.info("Broker authenticated")
        except Exception as e:
            logger.error("Broker authentication failed: %s", e)
            raise

    async def _reconcile_open_orders(self, repo: Repository) -> None:
        try:
            broker_orders = await self._broker.get_orders()
            logger.info("Reconciling %d open orders from broker", len(broker_orders))

            for broker_order in broker_orders:
                if broker_order.status in ("OPEN", "PARTIALLY_FILLED"):
                    existing = await repo.get_order(broker_order.id)
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
                        await repo.save_order(db_order)
                        logger.info(
                            "Reconciled orphan order %s for %s",
                            broker_order.id,
                            broker_order.symbol,
                        )

                    self._active_orders.add(broker_order.id)
        except Exception as e:
            logger.warning("Order reconciliation failed: %s", e)

    async def _is_market_open(self) -> bool:
        now = datetime.now(UTC)
        today = now.date()
        market_open_str = self._config.execution.market_open
        market_close_str = self._config.execution.market_close

        try:
            open_h, open_m = map(int, market_open_str.split(":"))
            close_h, close_m = map(int, market_close_str.split(":"))
        except (ValueError, AttributeError):
            return True

        market_open_dt = datetime.combine(today, time(open_h, open_m), tzinfo=UTC)
        market_close_dt = datetime.combine(today, time(close_h, close_m), tzinfo=UTC)

        return market_open_dt <= now <= market_close_dt

    async def _shutdown(self) -> None:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        logger.info("Shutdown requested, completing current cycle...")

    async def _cleanup(self, order_watcher: OrderWatcher, session: Any) -> None:
        logger.info("Cleaning up execution engine")
        try:
            await order_watcher.stop()
        except Exception as e:
            logger.warning("Error stopping order watcher: %s", e)

        if self._notifier:
            try:
                await self._notifier.wait_all(timeout=2.0)
            except Exception as e:
                logger.warning("Error waiting for notifications: %s", e)

        try:
            await session.close()
        except Exception as e:
            logger.warning("Error closing DB session: %s", e)

        logger.info("Execution engine shutdown complete")

    def activate_kill_switch(self) -> None:
        if self._safety_gate:
            self._safety_gate.kill_switch.activate()

    def deactivate_kill_switch(self) -> None:
        if self._safety_gate:
            self._safety_gate.kill_switch.deactivate()
