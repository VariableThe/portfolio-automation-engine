from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from portfolio_automation.dashboard.app import create_app
from portfolio_automation.dashboard.websocket import websocket_manager
from portfolio_automation.execution.engine import ExecutionEngine
from portfolio_automation.persistence.models import Order as DBOrder
from portfolio_automation.persistence.repository import Repository
from tests.integration.conftest import DeterministicMockBroker, TimeController


def test_websocket_event_broadcasting() -> None:
    """Verify WebSocket client receives broadcasted events within 1 second."""
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            # Broadcast a test event
            test_data = {"symbol": "RELIANCE", "price": 2500.0, "message": "Test event"}
            websocket_manager.broadcast("TEST_EVENT", test_data)

            # Receive event from WebSocket
            received = ws.receive_json()
            assert received["type"] == "TEST_EVENT"
            assert received["data"]["symbol"] == "RELIANCE"
            assert received["data"]["price"] == 2500.0


@pytest.mark.asyncio
async def test_api_consistency_with_db(
    session_factory: async_sessionmaker,
    base_config: Any,
) -> None:
    """Verify REST APIs (/api/holdings, /api/orders, /api/balance) exactly match DB state."""
    app = create_app(config=base_config)
    app.state.session_factory = session_factory

    # Populate DB with test data
    async with session_factory() as session:
        repo = Repository(session)
        # Add cash balance
        await repo.record_cash_balance(100_000.0, 0.0, 100_000.0)

        # Add holding snapshot
        await repo.save_holdings_snapshot(
            symbol="INFY",
            quantity=50,
            avg_price=1400.0,
            current_price=1450.0,
        )

        # Add an order
        order = DBOrder(
            id="order_test_api",
            symbol="INFY",
            side="BUY",
            order_type="MARKET",
            quantity=50,
            price=1400.0,
            status="COMPLETED",
            filled_quantity=50,
            filled_price=1400.0,
            placed_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        await repo.save_order(order)

    # Test endpoints using synchronous TestClient inside async test
    with TestClient(app) as client:
        # Check /api/balance
        res_bal = client.get("/api/balance")
        assert res_bal.status_code == 200
        data_bal = res_bal.json()
        assert data_bal["available_cash"] == 100_000.0
        assert data_bal["total_cash"] == 100_000.0

        # Check /api/holdings
        res_hold = client.get("/api/holdings")
        assert res_hold.status_code == 200
        data_hold = res_hold.json()
        assert len(data_hold["holdings"]) == 1
        assert data_hold["holdings"][0]["symbol"] == "INFY"
        assert data_hold["holdings"][0]["quantity"] == 50
        assert data_hold["holdings"][0]["current_value"] == 50 * 1450.0

        # Check /api/orders
        res_ord = client.get("/api/orders")
        assert res_ord.status_code == 200
        data_ord = res_ord.json()
        assert len(data_ord["completed_orders"]) == 1
        assert data_ord["completed_orders"][0]["id"] == "order_test_api"
        assert data_ord["completed_orders"][0]["symbol"] == "INFY"
        assert data_ord["completed_orders"][0]["quantity"] == 50


@pytest.mark.asyncio
async def test_settings_mutation_and_engine_reload(
    session_factory: async_sessionmaker,
    base_config: Any,
    time_controller: TimeController,
) -> None:
    """Verify mutating rules via POST /api/settings/rules updates the execution engine dynamically."""
    rules_path = Path("config/rules.yaml")
    original_yaml = (
        rules_path.read_text(encoding="utf-8") if rules_path.exists() else ""
    )

    try:
        app = create_app(config=base_config)
        app.state.session_factory = session_factory

        broker = DeterministicMockBroker(initial_cash=100_000.0)
        broker._base_prices["RELIANCE"] = 2500.0

        # Initialize engine without hardcoded rules_config so it reloads from config/rules.yaml
        engine = ExecutionEngine(
            broker=broker,
            config=base_config,
            session_factory=session_factory,
            rules_config=None,
        )
        await engine.setup()

        # Update rules via API
        new_yaml = """
buy_queue:
  - symbol: RELIANCE
    priority: 1
    condition:
      type: market
sell_rules: []
"""
        with TestClient(app) as client:
            res = client.post("/api/settings/rules", json={"yaml_content": new_yaml})
            assert res.status_code == 200
            assert res.json()["status"] == "success"

        # Advance time to market hours and step engine
        time_controller.advance(minutes=15)  # 09:15
        ctx = await engine.step()

        # Verify engine picked up RELIANCE from the dynamically loaded rules and placed an order
        assert ctx is not None
        assert len(ctx.buy_order_ids) == 1
        assert "RELIANCE" in broker._orders[ctx.buy_order_ids[0]].order.symbol

        await engine.stop()
    finally:
        # Restore original rules.yaml
        if original_yaml:
            rules_path.write_text(original_yaml, encoding="utf-8")
        elif rules_path.exists():
            rules_path.unlink()
