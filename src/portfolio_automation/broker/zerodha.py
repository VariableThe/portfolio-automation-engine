from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, cast

from portfolio_automation.broker.models import Balance, Holding, Order, Position
from portfolio_automation.core.config import BrokerConfig
from portfolio_automation.core.exceptions import (
    BrokerAuthenticationError,
    BrokerError,
    BrokerNetworkError,
    BrokerOrderRejectedError,
    BrokerSymbolNotFoundError,
)

_KITE_EXCHANGE = "NSE"


class ZerodhaBroker:
    def __init__(self, config: BrokerConfig) -> None:
        self._api_key = config.api_key
        self._api_secret = config.api_secret
        self._user_id = config.user_id
        self._access_token = config.access_token
        self._exchange = _KITE_EXCHANGE
        self._kite: Any = None

    async def authenticate(self) -> None:
        try:
            import kiteconnect
            from kiteconnect.exceptions import KiteException, TokenException
        except ImportError:
            raise BrokerError(
                "kiteconnect package not installed. Run: uv sync --extra zerodha"
            )

        def _login() -> Any:
            kite = kiteconnect.KiteConnect(api_key=self._api_key)
            if not self._access_token:
                raise BrokerAuthenticationError(
                    "No access token provided. Complete the OAuth flow first."
                )
            kite.set_access_token(self._access_token)
            return kite

        try:
            self._kite = await asyncio.to_thread(_login)
        except TokenException as e:
            raise BrokerAuthenticationError(f"Token invalid or expired: {e}") from e
        except KiteException as e:
            raise BrokerAuthenticationError(f"Kite authentication failed: {e}") from e

    # ── Mapping helpers ─────────────────────────────────────────────────

    def _map_holding(self, raw: dict[str, Any]) -> Holding:
        return Holding(
            symbol=str(raw.get("tradingsymbol", "")),
            quantity=int(raw.get("quantity", 0)),
            avg_price=float(raw.get("average_price", 0)),
            current_price=float(raw.get("last_price", 0)),
            pnl=float(raw.get("pnl", 0)),
        )

    def _map_position(self, raw: dict[str, Any]) -> Position:
        return Position(
            symbol=str(raw.get("tradingsymbol", "")),
            quantity=int(raw.get("quantity", 0)),
            avg_price=float(raw.get("average_price", 0)),
            current_price=float(raw.get("last_price", 0)),
            pnl=float(raw.get("pnl", 0)),
            day_buy_quantity=int(raw.get("day_buy_quantity", 0)),
            day_sell_quantity=int(raw.get("day_sell_quantity", 0)),
        )

    def _map_order(self, raw: dict[str, Any]) -> Order:
        placed_at = raw.get("placed_at") or raw.get("exchange_timestamp")
        completed_at = raw.get("completed_at") or raw.get("exchange_update_timestamp")

        def _parse_dt(val: Any) -> datetime | None:
            if isinstance(val, datetime):
                return val
            if isinstance(val, str):
                try:
                    return datetime.fromisoformat(val)
                except (ValueError, TypeError):
                    return None
            return None

        return Order(
            id=str(raw.get("order_id", "")),
            symbol=str(raw.get("tradingsymbol", "")),
            side=str(raw.get("transaction_type", "")).upper(),
            order_type=str(raw.get("order_type", "")),
            quantity=int(raw.get("quantity", 0)),
            price=float(raw["price"]) if raw.get("price") is not None else None,
            status=str(raw.get("status", "")).upper(),
            filled_quantity=int(raw.get("filled_quantity", 0)),
            filled_price=(
                float(raw["average_price"])
                if raw.get("average_price") is not None
                else None
            ),
            placed_at=_parse_dt(placed_at),
            completed_at=_parse_dt(completed_at),
            error_message=raw.get("status_message"),
        )

    def _map_balance(self, raw: dict[str, Any]) -> Balance:
        available = raw.get("available", {})
        return Balance(
            available_cash=float(available.get("cash", 0)),
            used_cash=float(raw.get("utilised", {}).get("dealer", 0)),
            total_cash=(
                float(available.get("cash", 0))
                + float(raw.get("utilised", {}).get("dealer", 0))
            ),
        )

    def _symbol_key(self, symbol: str) -> str:
        return f"{self._exchange}:{symbol}"

    # ── Broker Protocol ─────────────────────────────────────────────────

    async def get_holdings(self) -> list[Holding]:
        self._require_kite()
        try:
            raw = await asyncio.to_thread(self._kite.holdings)
            return [self._map_holding(item) for item in (raw or [])]
        except Exception as e:
            raise self._translate_error(e) from e

    async def get_positions(self) -> list[Position]:
        self._require_kite()
        try:
            raw = await asyncio.to_thread(self._kite.positions)
            positions = raw.get("net", []) if isinstance(raw, dict) else (raw or [])
            return [self._map_position(item) for item in positions]
        except Exception as e:
            raise self._translate_error(e) from e

    async def get_balance(self) -> Balance:
        self._require_kite()
        try:
            raw = await asyncio.to_thread(self._kite.margins)
            return self._map_balance(raw)
        except Exception as e:
            raise self._translate_error(e) from e

    async def get_orders(self) -> list[Order]:
        self._require_kite()
        try:
            raw = await asyncio.to_thread(self._kite.orders)
            return [self._map_order(item) for item in (raw or [])]
        except Exception as e:
            raise self._translate_error(e) from e

    async def get_price(self, symbol: str) -> float:
        self._require_kite()
        key = self._symbol_key(symbol)
        try:
            raw = await asyncio.to_thread(self._kite.ltp, key)
            data = raw.get(key, {}) if isinstance(raw, dict) else {}
            return float(data.get("last_price", 0))
        except Exception as e:
            raise self._translate_error(e) from e

    async def place_buy_order(
        self,
        symbol: str,
        quantity: int,
        order_type: str,
        price: float | None = None,
    ) -> Order:
        self._require_kite()
        try:
            order_id = await asyncio.to_thread(
                self._kite.place_order,
                variety="regular",
                exchange=self._exchange,
                tradingsymbol=symbol,
                transaction_type="BUY",
                quantity=quantity,
                product="CNC",
                order_type=order_type,
                price=price or 0,
            )
            return await self.get_order_status(order_id)
        except Exception as e:
            raise self._translate_error(e) from e

    async def place_sell_order(
        self,
        symbol: str,
        quantity: int,
        order_type: str,
        price: float | None = None,
    ) -> Order:
        self._require_kite()
        try:
            order_id = await asyncio.to_thread(
                self._kite.place_order,
                variety="regular",
                exchange=self._exchange,
                tradingsymbol=symbol,
                transaction_type="SELL",
                quantity=quantity,
                product="CNC",
                order_type=order_type,
                price=price or 0,
            )
            return await self.get_order_status(order_id)
        except Exception as e:
            raise self._translate_error(e) from e

    async def cancel_order(self, order_id: str) -> None:
        self._require_kite()
        try:
            await asyncio.to_thread(self._kite.cancel_order, order_id=order_id)
        except Exception as e:
            raise self._translate_error(e) from e

    async def get_order_status(self, order_id: str) -> Order:
        self._require_kite()
        try:
            raw = await asyncio.to_thread(self._kite.order_history, order_id)
            if not raw:
                raise BrokerOrderRejectedError(f"Order '{order_id}' not found.")
            return self._map_order(raw[-1])
        except Exception as e:
            raise self._translate_error(e) from e

    # ── Internals ───────────────────────────────────────────────────────

    def _require_kite(self) -> None:
        if self._kite is None:
            raise BrokerAuthenticationError(
                "Not authenticated. Call authenticate() first."
            )

    def _translate_error(self, error: Exception) -> BrokerError:
        try:
            from kiteconnect.exceptions import (
                KiteException,
                OrderException,
                TokenException,
            )
        except ImportError:
            return BrokerError(str(error))

        if isinstance(error, TokenException):
            return BrokerAuthenticationError(f"Session expired: {error}")
        if isinstance(error, OrderException):
            return BrokerOrderRejectedError(str(error))
        if isinstance(error, KiteException):
            msg = str(error)
            if "symbol" in msg.lower() or "not found" in msg.lower():
                return BrokerSymbolNotFoundError(msg)
            return BrokerNetworkError(msg)
        if isinstance(error, BrokerError):
            return cast(BrokerError, error)
        return BrokerNetworkError(f"Unexpected broker error: {error}")
