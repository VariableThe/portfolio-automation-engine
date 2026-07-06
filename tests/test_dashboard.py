from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from portfolio_automation.core.config import load_config
from portfolio_automation.dashboard.app import create_app
from portfolio_automation.dashboard.websocket import websocket_manager
from portfolio_automation.execution.engine import ExecutionEngine
from portfolio_automation.persistence.database import (
    create_engine,
    create_session_factory,
)
from portfolio_automation.persistence.models import Order as DBOrder
from portfolio_automation.persistence.repository import Repository
from portfolio_automation.safety.gate import SafetyGate


@pytest_asyncio.fixture
async def test_app(tmp_path):
    os.environ["DASHBOARD_API_KEY"] = "test-secret-key"
    cfg = load_config()
    cfg.database.path = str(tmp_path / "dashboard_test.db")
    app = create_app(cfg)
    engine = create_engine(cfg)
    async with engine.begin() as conn:
        from portfolio_automation.persistence.models import Base

        await conn.run_sync(Base.metadata.create_all)
    app.state.db_engine = engine
    app.state.session_factory = create_session_factory(engine)
    async with app.state.session_factory() as session:
        repo = Repository(session)
        app.state.safety_gate = SafetyGate(repository=repo)
    app.state.automation_enabled = True
    yield app
    await engine.dispose()


@pytest.fixture
def client(test_app):
    with TestClient(test_app) as c:
        yield c


@pytest.mark.asyncio
async def test_get_pages(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        res_dash = await client.get("/")
        assert res_dash.status_code == 200
        assert "Portfolio Holdings" in res_dash.text

        res_settings = await client.get("/settings")
        assert res_settings.status_code == 200
        assert "System Configuration" in res_settings.text

        res_history = await client.get("/history")
        assert res_history.status_code == 200
        assert "Trade History" in res_history.text

        res_health = await client.get("/health")
        assert res_health.status_code == 200
        assert res_health.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_get_apis_from_database(test_app):
    session_factory = test_app.state.session_factory
    async with session_factory() as session:
        repo = Repository(session)
        await repo.record_cash_balance(
            available_cash=80000.0, used_cash=20000.0, total_cash=100000.0
        )
        await repo.save_holdings_snapshot(
            symbol="RELIANCE", quantity=10, avg_price=2000.0, current_price=2200.0
        )
        order = DBOrder(
            id="ord-1",
            symbol="RELIANCE",
            side="BUY",
            order_type="MARKET",
            quantity=10,
            price=2000.0,
            status="COMPLETED",
            filled_quantity=10,
            filled_price=2000.0,
            placed_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        await repo.save_order(order)
        await repo.save_log(
            level="INFO", module="test", message="Dashboard integration log"
        )

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        res_bal = await client.get("/api/balance")
        assert res_bal.status_code == 200
        assert res_bal.json()["available_cash"] == 80000.0

        res_holdings = await client.get("/api/holdings")
        assert res_holdings.status_code == 200
        h_json = res_holdings.json()
        assert len(h_json["holdings"]) == 1
        assert h_json["holdings"][0]["symbol"] == "RELIANCE"
        assert h_json["holdings"][0]["pnl"] == 2000.0

        res_orders = await client.get("/api/orders")
        assert res_orders.status_code == 200
        o_json = res_orders.json()
        assert len(o_json["completed_orders"]) == 1
        assert o_json["completed_orders"][0]["symbol"] == "RELIANCE"

        res_logs = await client.get("/api/logs")
        assert res_logs.status_code == 200
        assert len(res_logs.json()["logs"]) >= 1

        res_bq = await client.get("/api/buy-queue")
        assert res_bq.status_code == 200
        assert "buy_queue" in res_bq.json()

        res_sr = await client.get("/api/sell-rules")
        assert res_sr.status_code == 200
        assert "sell_rules" in res_sr.json()


@pytest.mark.asyncio
async def test_sensitive_endpoints_require_api_key(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        res = await client.post("/api/kill-switch", json={"active": True})
        assert res.status_code == 401

        res = await client.post(
            "/api/kill-switch",
            headers={"X-API-Key": "wrong-key"},
            json={"active": True},
        )
        assert res.status_code == 401

        res = await client.post(
            "/api/kill-switch",
            headers={"X-API-Key": "test-secret-key"},
            json={"active": True},
        )
        assert res.status_code == 200
        assert res.json()["active"] is True

        res = await client.post(
            "/api/manual-confirmation/TCS", json={"action": "approve"}
        )
        assert res.status_code == 401

        res = await client.post(
            "/api/manual-confirmation/TCS",
            headers={"X-API-Key": "test-secret-key"},
            json={"action": "approve"},
        )
        assert res.status_code == 200
        assert res.json()["symbol"] == "TCS"


@pytest.mark.asyncio
async def test_settings_mutations(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        res_auto = await client.post(
            "/api/settings/automation", json={"enabled": False}
        )
        assert res_auto.status_code == 200
        assert res_auto.json()["enabled"] is False

        res_dry = await client.post("/api/settings/dry-run", json={"enabled": True})
        assert res_dry.status_code == 200
        assert res_dry.json()["enabled"] is True

        yaml_data = """
buy_queue:
  - symbol: HDFC
    priority: 1
    condition:
      type: rsi
      params:
        period: 14
        lower: 30
sell_rules: []
"""
        res_rules = await client.post(
            "/api/settings/rules", json={"yaml_content": yaml_data}
        )
        assert res_rules.status_code == 200, res_rules.text


def test_websocket_broadcast_and_engine_integration(test_app):
    with TestClient(test_app) as client:
        with client.websocket_connect("/ws") as ws:
            websocket_manager.broadcast("TEST_EVENT", {"message": "Hello WS"})
            data = ws.receive_json()
            assert data["type"] == "TEST_EVENT"
            assert data["data"]["message"] == "Hello WS"

            mock_broker = MagicMock()
            engine = ExecutionEngine(
                broker=mock_broker,
                config=test_app.state.config,
                session_factory=test_app.state.session_factory,
                broadcaster=websocket_manager,
            )
            engine.broadcast_event("ORDER_PLACED", {"symbol": "INFY", "quantity": 10})
            data2 = ws.receive_json()
            assert data2["type"] == "ORDER_PLACED"
            assert data2["data"]["symbol"] == "INFY"
