from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from portfolio_automation.broker.interface import Broker
from portfolio_automation.broker.models import Balance, Holding
from portfolio_automation.execution.buy_manager import BuyManager
from portfolio_automation.execution.order_watcher import OrderWatcher
from portfolio_automation.execution.sell_manager import SellManager
from portfolio_automation.persistence.repository import Repository
from portfolio_automation.rules.engine import RuleEngine
from portfolio_automation.rules.models import RulesConfig

logger = logging.getLogger(__name__)

SELL_FILL_TIMEOUT = 60.0


@dataclass
class CycleContext:
    cycle_id: str
    holdings: list[Holding] = field(default_factory=list)
    balance: Balance | None = None
    prices: dict[str, float] = field(default_factory=dict)
    sell_order_ids: list[str] = field(default_factory=list)
    buy_order_ids: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


async def execute_cycle(
    broker: Broker,
    rule_engine: RuleEngine,
    rules_config: RulesConfig,
    repository: Repository,
    order_watcher: OrderWatcher,
    sell_manager: SellManager,
    buy_manager: BuyManager,
    active_orders: set[str],
    candle_data: dict[str, list[dict[str, Any]]] | None = None,
) -> CycleContext:
    cycle_id = datetime.now(UTC).strftime("cycle_%Y%m%d_%H%M%S_%f")
    ctx = CycleContext(cycle_id=cycle_id)
    logger.info("Starting cycle %s", cycle_id)

    try:
        holdings = await broker.get_holdings()
        ctx.holdings = holdings
        logger.info("Fetched %d holdings", len(holdings))
    except Exception as e:
        logger.error("Failed to fetch holdings: %s", e)
        return ctx

    try:
        balance = await broker.get_balance()
        ctx.balance = balance
        logger.info(
            "Balance: available=%.2f, total=%.2f",
            balance.available_cash,
            balance.total_cash,
        )
    except Exception as e:
        logger.error("Failed to fetch balance: %s", e)
        return ctx

    symbols: set[str] = {h.symbol for h in holdings}
    for entry in rules_config.buy_queue:
        symbols.add(entry.symbol)

    prices: dict[str, float] = {}
    for symbol in symbols:
        try:
            prices[symbol] = await broker.get_price(symbol)
        except Exception as e:
            logger.warning("Failed to get price for %s: %s", symbol, e)
    ctx.prices = prices
    logger.info("Fetched prices for %d symbols", len(prices))

    sell_order_ids = await sell_manager.evaluate_and_sell(
        holdings=holdings,
        prices=prices,
        portfolio_value=balance.total_cash,
        available_cash=balance.available_cash,
        cycle_id=cycle_id,
        candles=candle_data,
    )
    ctx.sell_order_ids = sell_order_ids

    if sell_order_ids:
        logger.info("Waiting for %d sell orders to fill", len(sell_order_ids))
        for order_id in sell_order_ids:
            try:
                filled_order = await order_watcher.wait_for_terminal(
                    order_id, timeout=SELL_FILL_TIMEOUT
                )
                logger.info(
                    "Sell order %s completed: status=%s, filled=%d/%d",
                    order_id,
                    filled_order.status,
                    filled_order.filled_quantity,
                    filled_order.quantity,
                )
            except TimeoutError:
                logger.warning(
                    "Sell order %s did not fill within %ds, continuing",
                    order_id,
                    SELL_FILL_TIMEOUT,
                )
            except Exception as e:
                logger.error("Error waiting for sell order %s: %s", order_id, e)

        try:
            fresh_balance = await broker.get_balance()
            ctx.balance = fresh_balance
            logger.info(
                "Refreshed balance after sells: available=%.2f",
                fresh_balance.available_cash,
            )
        except Exception as e:
            logger.warning("Failed to refresh balance after sells: %s", e)

    buy_order_ids: list[str] = []
    if rules_config.buy_queue and ctx.balance is not None:
        buy_order_ids = await buy_manager.process_buy_queue(
            buy_queue=rules_config.buy_queue,
            balance=ctx.balance,
            prices=prices,
            holdings=holdings,
            cycle_id=cycle_id,
            candle_data=candle_data,
        )
    ctx.buy_order_ids = buy_order_ids

    try:
        await save_cycle_snapshot(repository, ctx, prices)
    except Exception as e:
        logger.error("Failed to save cycle snapshot: %s", e)

    logger.info(
        "Cycle %s complete: %d sells, %d buys",
        cycle_id,
        len(sell_order_ids),
        len(buy_order_ids),
    )
    return ctx


async def save_cycle_snapshot(
    repository: Repository,
    ctx: CycleContext,
    prices: dict[str, float],
) -> None:
    for holding in ctx.holdings:
        current_price = prices.get(holding.symbol, holding.current_price)
        await repository.save_holdings_snapshot(
            symbol=holding.symbol,
            quantity=holding.quantity,
            avg_price=holding.avg_price,
            current_price=current_price,
            cycle_id=ctx.cycle_id,
        )

    if ctx.balance is not None:
        await repository.record_cash_balance(
            available_cash=ctx.balance.available_cash,
            used_cash=ctx.balance.used_cash,
            total_cash=ctx.balance.total_cash,
        )
