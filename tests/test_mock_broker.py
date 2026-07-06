from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from portfolio_automation.broker.mock import MockBroker
from portfolio_automation.broker.zerodha import ZerodhaBroker
from portfolio_automation.core.config import BrokerConfig
from portfolio_automation.core.exceptions import (
    BrokerAuthenticationError,
    BrokerOrderRejectedError,
)

# ── Mock Broker State Tests ────────────────────────────────────────────────


@pytest.fixture
def broker() -> MockBroker:
    return MockBroker(
        initial_cash=100_000.0,
        latency=0.0,
        fill_delay=0.05,
        partial_fill_enabled=False,
        reject_probability=0.0,
    )


@pytest.mark.asyncio
async def test_mock_internal_state_after_buy(broker: MockBroker) -> None:
    await broker.authenticate()
    await broker.place_buy_order("HAL", 10, "MARKET", None)
    await asyncio.sleep(0.15)

    assert broker._cash < 100_000.0
    assert "HAL" in broker._holdings
    assert broker._holdings["HAL"]["quantity"] == 10


@pytest.mark.asyncio
async def test_mock_internal_state_after_sell(broker: MockBroker) -> None:
    await broker.authenticate()
    cash_before = broker._cash
    infy_qty_before = broker._holdings["INFY"]["quantity"]

    await broker.place_sell_order("INFY", 5, "MARKET", None)
    await asyncio.sleep(0.15)

    assert broker._cash > cash_before
    assert broker._holdings["INFY"]["quantity"] == infy_qty_before - 5


@pytest.mark.asyncio
async def test_mock_full_sell_removes_holding(broker: MockBroker) -> None:
    await broker.authenticate()
    await broker.place_sell_order("TCS", 5, "MARKET", None)
    await asyncio.sleep(0.15)

    assert "TCS" not in broker._holdings


@pytest.mark.asyncio
async def test_mock_rejected_order_no_state_change(broker: MockBroker) -> None:
    broker._reject_probability = 1.0
    await broker.authenticate()
    cash_before = broker._cash

    with pytest.raises(BrokerOrderRejectedError):
        await broker.place_buy_order("HAL", 10, "MARKET", None)

    assert broker._cash == cash_before
    assert "HAL" not in broker._holdings


@pytest.mark.asyncio
async def test_mock_balance_reflects_pending_orders(broker: MockBroker) -> None:
    broker._reject_probability = 0.0
    await broker.authenticate()

    balance_before = await broker.get_balance()
    await broker.place_buy_order("HAL", 5, "MARKET", 5000.0)

    balance_during = await broker.get_balance()
    assert balance_during.available_cash <= balance_before.available_cash

    await asyncio.sleep(0.15)
    balance_after = await broker.get_balance()
    assert balance_after.available_cash < balance_before.available_cash


@pytest.mark.asyncio
async def test_mock_multiple_orders(broker: MockBroker) -> None:
    await broker.authenticate()
    o1 = await broker.place_buy_order("HAL", 5, "MARKET", None)
    o2 = await broker.place_buy_order("BEL", 10, "MARKET", None)
    await asyncio.sleep(0.2)

    s1 = await broker.get_order_status(o1.id)
    s2 = await broker.get_order_status(o2.id)
    assert s1.status == "COMPLETED"
    assert s2.status == "COMPLETED"

    holdings = {h.symbol: h.quantity for h in await broker.get_holdings()}
    assert holdings.get("HAL", 0) >= 5
    assert holdings.get("BEL", 0) >= 10


# ── Zerodha Mapping Tests ──────────────────────────────────────────────────


def _mock_kite_order(order_id: str, status: str = "COMPLETE") -> dict:
    return {
        "order_id": order_id,
        "tradingsymbol": "INFY",
        "transaction_type": "BUY",
        "order_type": "MARKET",
        "quantity": 10,
        "price": 0,
        "status": status,
        "filled_quantity": 10,
        "average_price": 1550.0,
        "placed_at": "2026-07-03T10:00:00",
        "completed_at": "2026-07-03T10:01:00",
        "status_message": None,
    }


def _mock_kite_holding() -> dict:
    return {
        "tradingsymbol": "INFY",
        "quantity": 10,
        "average_price": 1500.0,
        "last_price": 1550.0,
        "pnl": 500.0,
    }


def _mock_kite_margins() -> dict:
    return {
        "available": {"cash": 50000.0},
        "utilised": {"dealer": 10000.0},
    }


@pytest.mark.asyncio
async def test_zerodha_get_holdings_maps_correctly() -> None:
    config = BrokerConfig(
        type="zerodha", api_key="key", api_secret="secret", user_id="user"
    )
    broker = ZerodhaBroker(config)
    broker._kite = MagicMock()
    broker._kite.holdings = MagicMock(return_value=[_mock_kite_holding()])

    holdings = await broker.get_holdings()
    assert len(holdings) == 1
    h = holdings[0]
    assert h.symbol == "INFY"
    assert h.quantity == 10
    assert h.avg_price == 1500.0
    assert h.current_price == 1550.0
    assert h.pnl == 500.0


@pytest.mark.asyncio
async def test_zerodha_get_balance_maps_correctly() -> None:
    config = BrokerConfig(
        type="zerodha", api_key="key", api_secret="secret", user_id="user"
    )
    broker = ZerodhaBroker(config)
    broker._kite = MagicMock()
    broker._kite.margins = MagicMock(return_value=_mock_kite_margins())

    balance = await broker.get_balance()
    assert balance.available_cash == 50000.0
    assert balance.used_cash == 10000.0
    assert balance.total_cash == 60000.0


@pytest.mark.asyncio
async def test_zerodha_get_order_status_maps_correctly() -> None:
    config = BrokerConfig(
        type="zerodha", api_key="key", api_secret="secret", user_id="user"
    )
    broker = ZerodhaBroker(config)
    broker._kite = MagicMock()
    broker._kite.order_history = MagicMock(
        return_value=[_mock_kite_order("order_123", "COMPLETE")]
    )

    order = await broker.get_order_status("order_123")
    assert order.id == "order_123"
    assert order.symbol == "INFY"
    assert order.side == "BUY"
    assert order.status == "COMPLETE"
    assert order.filled_quantity == 10
    assert order.filled_price == 1550.0
    assert order.price == 0


@pytest.mark.asyncio
async def test_zerodha_place_buy_order() -> None:
    config = BrokerConfig(
        type="zerodha", api_key="key", api_secret="secret", user_id="user"
    )
    broker = ZerodhaBroker(config)
    broker._kite = MagicMock()
    broker._kite.place_order = MagicMock(return_value="order_456")
    broker._kite.order_history = MagicMock(
        return_value=[_mock_kite_order("order_456", "COMPLETE")]
    )

    order = await broker.place_buy_order("INFY", 10, "MARKET", None)
    assert order.id == "order_456"
    broker._kite.place_order.assert_called_once_with(
        variety="regular",
        exchange="NSE",
        tradingsymbol="INFY",
        transaction_type="BUY",
        quantity=10,
        product="CNC",
        order_type="MARKET",
        price=0,
    )


@pytest.mark.asyncio
async def test_zerodha_place_sell_order() -> None:
    config = BrokerConfig(
        type="zerodha", api_key="key", api_secret="secret", user_id="user"
    )
    broker = ZerodhaBroker(config)
    broker._kite = MagicMock()
    broker._kite.place_order = MagicMock(return_value="order_789")
    broker._kite.order_history = MagicMock(
        return_value=[_mock_kite_order("order_789", "COMPLETE")]
    )

    order = await broker.place_sell_order("INFY", 5, "LIMIT", 1600.0)
    assert order.id == "order_789"
    broker._kite.place_order.assert_called_once_with(
        variety="regular",
        exchange="NSE",
        tradingsymbol="INFY",
        transaction_type="SELL",
        quantity=5,
        product="CNC",
        order_type="LIMIT",
        price=1600.0,
    )


@pytest.mark.asyncio
async def test_zerodha_unauthenticated_raises() -> None:
    config = BrokerConfig(
        type="zerodha", api_key="key", api_secret="secret", user_id="user"
    )
    broker = ZerodhaBroker(config)

    with pytest.raises(BrokerAuthenticationError):
        await broker.get_holdings()


@pytest.mark.asyncio
async def test_zerodha_translates_token_exception() -> None:
    from kiteconnect.exceptions import TokenException

    config = BrokerConfig(
        type="zerodha", api_key="key", api_secret="secret", user_id="user"
    )
    broker = ZerodhaBroker(config)
    broker._kite = MagicMock()
    broker._kite.holdings = MagicMock(side_effect=TokenException("token expired"))

    with pytest.raises(BrokerAuthenticationError):
        await broker.get_holdings()
