from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from portfolio_automation.broker.zerodha import ZerodhaBroker
from portfolio_automation.core.config import BrokerConfig
from portfolio_automation.core.exceptions import (
    BrokerAuthenticationError,
    BrokerNetworkError,
    BrokerOrderRejectedError,
    BrokerSymbolNotFoundError,
)


@pytest.fixture
def mock_kite_module():
    """Create a fake kiteconnect module with exception classes."""
    module = ModuleType("kiteconnect")

    class KiteException(Exception):
        pass

    class TokenException(KiteException):
        pass

    class OrderException(KiteException):
        pass

    class NetworkException(KiteException):
        pass

    module.KiteConnect = MagicMock()

    exceptions_module = ModuleType("kiteconnect.exceptions")
    exceptions_module.KiteException = KiteException
    exceptions_module.TokenException = TokenException
    exceptions_module.OrderException = OrderException
    exceptions_module.NetworkException = NetworkException
    module.exceptions = exceptions_module

    with patch.dict(
        sys.modules,
        {
            "kiteconnect": module,
            "kiteconnect.exceptions": exceptions_module,
        },
    ):
        yield module


@pytest.fixture
def broker_config():
    return BrokerConfig(
        provider="zerodha",
        api_key="test_api_key",
        api_secret="test_api_secret",
        access_token="test_access_token",
        user_id="test_user",
    )


@pytest.mark.asyncio
async def test_authenticate_success(broker_config, mock_kite_module):
    mock_kite_instance = MagicMock()
    mock_kite_module.KiteConnect.return_value = mock_kite_instance

    broker = ZerodhaBroker(broker_config)
    await broker.authenticate()

    mock_kite_module.KiteConnect.assert_called_once_with(api_key="test_api_key")
    mock_kite_instance.set_access_token.assert_called_once_with("test_access_token")
    assert broker._kite is mock_kite_instance


@pytest.mark.asyncio
async def test_authenticate_no_access_token(mock_kite_module):
    cfg = BrokerConfig(provider="zerodha", api_key="test_api_key")
    broker = ZerodhaBroker(cfg)
    with pytest.raises(BrokerAuthenticationError, match="No access token provided"):
        await broker.authenticate()


@pytest.mark.asyncio
async def test_authenticate_token_exception(broker_config, mock_kite_module):
    mock_kite_instance = MagicMock()
    mock_kite_instance.set_access_token.side_effect = (
        mock_kite_module.exceptions.TokenException("Expired")
    )
    mock_kite_module.KiteConnect.return_value = mock_kite_instance

    broker = ZerodhaBroker(broker_config)
    with pytest.raises(BrokerAuthenticationError, match="Token invalid or expired"):
        await broker.authenticate()


@pytest.mark.asyncio
async def test_authenticate_kite_exception(broker_config, mock_kite_module):
    mock_kite_instance = MagicMock()
    mock_kite_instance.set_access_token.side_effect = (
        mock_kite_module.exceptions.KiteException("Auth fail")
    )
    mock_kite_module.KiteConnect.return_value = mock_kite_instance

    broker = ZerodhaBroker(broker_config)
    with pytest.raises(BrokerAuthenticationError, match="Kite authentication failed"):
        await broker.authenticate()


@pytest.mark.asyncio
async def test_require_kite_error(broker_config):
    broker = ZerodhaBroker(broker_config)
    with pytest.raises(BrokerAuthenticationError, match="Not authenticated"):
        await broker.get_holdings()


@pytest.mark.asyncio
async def test_get_holdings(broker_config, mock_kite_module):
    broker = ZerodhaBroker(broker_config)
    mock_kite = MagicMock()
    broker._kite = mock_kite

    mock_kite.holdings.return_value = [
        {
            "tradingsymbol": "INFY",
            "quantity": 100,
            "average_price": 1400.0,
            "last_price": 1450.0,
            "pnl": 5000.0,
        }
    ]

    holdings = await broker.get_holdings()
    assert len(holdings) == 1
    assert holdings[0].symbol == "INFY"
    assert holdings[0].quantity == 100
    assert holdings[0].current_price == 1450.0


@pytest.mark.asyncio
async def test_get_positions(broker_config, mock_kite_module):
    broker = ZerodhaBroker(broker_config)
    mock_kite = MagicMock()
    broker._kite = mock_kite

    mock_kite.positions.return_value = {
        "net": [
            {
                "tradingsymbol": "TCS",
                "quantity": 50,
                "average_price": 3500.0,
                "last_price": 3600.0,
                "pnl": 5000.0,
                "day_buy_quantity": 50,
                "day_sell_quantity": 0,
            }
        ]
    }

    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "TCS"
    assert positions[0].day_buy_quantity == 50


@pytest.mark.asyncio
async def test_get_balance(broker_config, mock_kite_module):
    broker = ZerodhaBroker(broker_config)
    mock_kite = MagicMock()
    broker._kite = mock_kite

    mock_kite.margins.return_value = {
        "available": {"cash": 100000.0},
        "utilised": {"dealer": 20000.0},
    }

    bal = await broker.get_balance()
    assert bal.available_cash == 100000.0
    assert bal.used_cash == 20000.0
    assert bal.total_cash == 120000.0


@pytest.mark.asyncio
async def test_get_orders(broker_config, mock_kite_module):
    broker = ZerodhaBroker(broker_config)
    mock_kite = MagicMock()
    broker._kite = mock_kite

    now_iso = datetime.now(UTC).isoformat()
    mock_kite.orders.return_value = [
        {
            "order_id": "ord-123",
            "tradingsymbol": "RELIANCE",
            "transaction_type": "BUY",
            "order_type": "MARKET",
            "quantity": 10,
            "price": 2500.0,
            "status": "COMPLETE",
            "filled_quantity": 10,
            "average_price": 2500.0,
            "placed_at": now_iso,
            "completed_at": now_iso,
        }
    ]

    orders = await broker.get_orders()
    assert len(orders) == 1
    assert orders[0].id == "ord-123"
    assert orders[0].status == "COMPLETE"
    assert orders[0].placed_at is not None


@pytest.mark.asyncio
async def test_get_price(broker_config, mock_kite_module):
    broker = ZerodhaBroker(broker_config)
    mock_kite = MagicMock()
    broker._kite = mock_kite

    mock_kite.ltp.return_value = {"NSE:INFY": {"last_price": 1500.50}}

    price = await broker.get_price("INFY")
    assert price == 1500.50
    mock_kite.ltp.assert_called_once_with("NSE:INFY")


@pytest.mark.asyncio
async def test_place_orders_and_status(broker_config, mock_kite_module):
    broker = ZerodhaBroker(broker_config)
    mock_kite = MagicMock()
    broker._kite = mock_kite

    mock_kite.place_order.return_value = "ord-buy-1"
    mock_kite.order_history.return_value = [
        {
            "order_id": "ord-buy-1",
            "tradingsymbol": "HAL",
            "transaction_type": "BUY",
            "order_type": "LIMIT",
            "quantity": 20,
            "price": 4000.0,
            "status": "OPEN",
            "filled_quantity": 0,
            "average_price": 0.0,
        }
    ]

    buy_ord = await broker.place_buy_order("HAL", 20, "LIMIT", price=4000.0)
    assert buy_ord.id == "ord-buy-1"
    assert buy_ord.side == "BUY"

    mock_kite.place_order.return_value = "ord-sell-1"
    mock_kite.order_history.return_value = [
        {
            "order_id": "ord-sell-1",
            "tradingsymbol": "HAL",
            "transaction_type": "SELL",
            "order_type": "MARKET",
            "quantity": 20,
            "price": 0.0,
            "status": "COMPLETE",
            "filled_quantity": 20,
            "average_price": 4100.0,
        }
    ]

    sell_ord = await broker.place_sell_order("HAL", 20, "MARKET")
    assert sell_ord.id == "ord-sell-1"
    assert sell_ord.side == "SELL"


@pytest.mark.asyncio
async def test_cancel_order(broker_config, mock_kite_module):
    broker = ZerodhaBroker(broker_config)
    mock_kite = MagicMock()
    broker._kite = mock_kite

    await broker.cancel_order("ord-123")
    mock_kite.cancel_order.assert_called_once_with(order_id="ord-123")


@pytest.mark.asyncio
async def test_get_order_status_not_found(broker_config, mock_kite_module):
    broker = ZerodhaBroker(broker_config)
    mock_kite = MagicMock()
    broker._kite = mock_kite

    mock_kite.order_history.return_value = []
    with pytest.raises(BrokerOrderRejectedError, match="not found"):
        await broker.get_order_status("ord-missing")


@pytest.mark.asyncio
async def test_error_translation(broker_config, mock_kite_module):
    broker = ZerodhaBroker(broker_config)
    mock_kite = MagicMock()
    broker._kite = mock_kite

    mock_kite.holdings.side_effect = mock_kite_module.exceptions.TokenException(
        "expired"
    )
    with pytest.raises(BrokerAuthenticationError, match="Session expired"):
        await broker.get_holdings()

    mock_kite.positions.side_effect = mock_kite_module.exceptions.OrderException(
        "margin short"
    )
    with pytest.raises(BrokerOrderRejectedError, match="margin short"):
        await broker.get_positions()

    mock_kite.orders.side_effect = mock_kite_module.exceptions.KiteException(
        "symbol INFY not found"
    )
    with pytest.raises(BrokerSymbolNotFoundError, match="symbol INFY not found"):
        await broker.get_orders()

    mock_kite.ltp.side_effect = mock_kite_module.exceptions.KiteException(
        "network timeout"
    )
    with pytest.raises(BrokerNetworkError, match="network timeout"):
        await broker.get_price("INFY")

    mock_kite.margins.side_effect = ValueError("unexpected value")
    with pytest.raises(BrokerNetworkError, match="Unexpected broker error"):
        await broker.get_balance()
