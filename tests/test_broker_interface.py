from __future__ import annotations

import asyncio

import pytest

from portfolio_automation.broker.interface import Broker
from portfolio_automation.broker.mock import MockBroker
from portfolio_automation.broker.models import Balance
from portfolio_automation.core.exceptions import (
    BrokerAuthenticationError,
    BrokerOrderRejectedError,
    BrokerSymbolNotFoundError,
)


@pytest.fixture
def broker() -> MockBroker:
    return MockBroker(
        initial_cash=100_000.0,
        latency=0.01,
        fill_delay=0.05,
        partial_fill_enabled=True,
        reject_probability=0.0,
    )


@pytest.mark.asyncio
async def test_protocol_conformance(broker: MockBroker) -> None:
    assert isinstance(broker, Broker), "MockBroker does not conform to Broker protocol"


@pytest.mark.asyncio
async def test_authenticate(broker: MockBroker) -> None:
    await broker.authenticate()
    holdings = await broker.get_holdings()
    assert len(holdings) > 0


@pytest.mark.asyncio
async def test_unauthenticated_raises(broker: MockBroker) -> None:
    with pytest.raises(BrokerAuthenticationError):
        await broker.get_holdings()


@pytest.mark.asyncio
async def test_get_holdings(broker: MockBroker) -> None:
    await broker.authenticate()
    holdings = await broker.get_holdings()
    assert len(holdings) >= 2
    infy = next(h for h in holdings if h.symbol == "INFY")
    assert infy.quantity == 10
    assert infy.avg_price == 1500.0
    assert infy.current_price is not None
    assert isinstance(infy.pnl, float)


@pytest.mark.asyncio
async def test_get_positions(broker: MockBroker) -> None:
    await broker.authenticate()
    positions = await broker.get_positions()
    symbols = {p.symbol for p in positions}
    assert "INFY" in symbols
    assert "TCS" in symbols


@pytest.mark.asyncio
async def test_get_balance(broker: MockBroker) -> None:
    await broker.authenticate()
    balance = await broker.get_balance()
    assert isinstance(balance, Balance)
    assert balance.available_cash == 100_000.0
    assert balance.total_cash == 100_000.0


@pytest.mark.asyncio
async def test_get_price(broker: MockBroker) -> None:
    await broker.authenticate()
    price = await broker.get_price("INFY")
    assert isinstance(price, float)
    assert price > 0


@pytest.mark.asyncio
async def test_get_price_unknown_symbol(broker: MockBroker) -> None:
    await broker.authenticate()
    with pytest.raises(BrokerSymbolNotFoundError):
        await broker.get_price("UNKNOWN")


@pytest.mark.asyncio
async def test_get_orders_empty(broker: MockBroker) -> None:
    await broker.authenticate()
    orders = await broker.get_orders()
    assert orders == []


@pytest.mark.asyncio
async def test_place_buy_order_updates_state(broker: MockBroker) -> None:
    await broker.authenticate()
    balance_before = await broker.get_balance()

    order = await broker.place_buy_order("HAL", 10, "MARKET", None)
    assert order.side == "BUY"
    assert order.symbol == "HAL"
    assert order.quantity == 10
    assert order.status == "OPEN"

    await asyncio.sleep(0.2)

    result = await broker.get_order_status(order.id)
    assert result.status == "COMPLETED"
    assert result.filled_quantity == 10

    balance_after = await broker.get_balance()
    assert balance_after.available_cash < balance_before.available_cash

    holdings = await broker.get_holdings()
    symbols = {h.symbol for h in holdings}
    assert "HAL" in symbols


@pytest.mark.asyncio
async def test_place_sell_order_updates_state(broker: MockBroker) -> None:
    await broker.authenticate()
    balance_before = await broker.get_balance()

    order = await broker.place_sell_order("INFY", 5, "MARKET", None)
    assert order.side == "SELL"
    assert order.symbol == "INFY"

    await asyncio.sleep(0.2)

    result = await broker.get_order_status(order.id)
    assert result.status == "COMPLETED"
    assert result.filled_quantity == 5

    balance_after = await broker.get_balance()
    assert balance_after.available_cash > balance_before.available_cash

    holdings = await broker.get_holdings()
    infy = next(h for h in holdings if h.symbol == "INFY")
    assert infy.quantity == 5


@pytest.mark.asyncio
async def test_place_sell_insufficient_shares(broker: MockBroker) -> None:
    await broker.authenticate()
    with pytest.raises(BrokerOrderRejectedError):
        await broker.place_sell_order("INFY", 999, "MARKET", None)


@pytest.mark.asyncio
async def test_place_buy_insufficient_funds(broker: MockBroker) -> None:
    await broker.authenticate()
    with pytest.raises(BrokerOrderRejectedError):
        await broker.place_buy_order("HAL", 999_999, "MARKET", 999_999)


@pytest.mark.asyncio
async def test_cancel_order(broker: MockBroker) -> None:
    await broker.authenticate()
    order = await broker.place_buy_order("HAL", 5, "MARKET", None)
    await broker.cancel_order(order.id)
    result = await broker.get_order_status(order.id)
    assert result.status == "CANCELLED"


@pytest.mark.asyncio
async def test_cancel_nonexistent_order(broker: MockBroker) -> None:
    await broker.authenticate()
    with pytest.raises(BrokerOrderRejectedError):
        await broker.cancel_order("NONEXISTENT")


@pytest.mark.asyncio
async def test_partial_fills(broker: MockBroker) -> None:
    broker._partial_fill_enabled = True
    broker._fill_delay = 0.05
    await broker.authenticate()

    order = await broker.place_buy_order("BEL", 5, "MARKET", None)

    await asyncio.sleep(0.08)

    result = await broker.get_order_status(order.id)
    assert result.status in ("OPEN", "PARTIALLY_FILLED", "COMPLETED")

    await asyncio.sleep(0.15)

    result = await broker.get_order_status(order.id)
    assert result.status == "COMPLETED"
    assert result.filled_quantity == 5


@pytest.mark.asyncio
async def test_buy_zero_quantity(broker: MockBroker) -> None:
    await broker.authenticate()
    with pytest.raises(BrokerOrderRejectedError):
        await broker.place_buy_order("INFY", 0, "MARKET", None)


@pytest.mark.asyncio
async def test_sell_zero_quantity(broker: MockBroker) -> None:
    await broker.authenticate()
    with pytest.raises(BrokerOrderRejectedError):
        await broker.place_sell_order("INFY", 0, "MARKET", None)


@pytest.mark.asyncio
async def test_unknown_symbol_buy(broker: MockBroker) -> None:
    await broker.authenticate()
    with pytest.raises(BrokerSymbolNotFoundError):
        await broker.place_buy_order("FAKE", 1, "MARKET", None)
