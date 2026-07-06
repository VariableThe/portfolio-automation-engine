from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from portfolio_automation.dashboard.websocket import websocket_manager
from portfolio_automation.notifications.interface import EventType
from portfolio_automation.persistence.repository import Repository
from portfolio_automation.rules.loader import (
    RuleLoadError,
    load_rules,
    save_rules_from_string,
)

logger = logging.getLogger(__name__)

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


async def get_repository(request: Request) -> AsyncGenerator[Repository]:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        yield Repository(session)


async def verify_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    api_key: str | None = Query(None),
) -> None:
    expected = os.getenv("DASHBOARD_API_KEY", "secret-api-key")
    provided = x_api_key or api_key
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ── Page Routes ─────────────────────────────────────────────────────────────


@router.get("/")
async def get_dashboard_page(request: Request) -> Any:
    return templates.TemplateResponse(request=request, name="dashboard.html")


@router.get("/settings")
async def get_settings_page(request: Request) -> Any:
    return templates.TemplateResponse(request=request, name="settings.html")


@router.get("/history")
async def get_history_page(request: Request) -> Any:
    return templates.TemplateResponse(request=request, name="history.html")


@router.get("/health")
async def health_check(request: Request) -> dict[str, Any]:
    from datetime import UTC, datetime

    from sqlalchemy import text

    session_factory = getattr(request.app.state, "session_factory", None)
    if not session_factory:
        raise HTTPException(
            status_code=503, detail="Database session factory not initialized"
        )

    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("Health check database failure: %s", e)
        raise HTTPException(
            status_code=503, detail="Database connectivity check failed"
        ) from e

    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ── API Endpoints ───────────────────────────────────────────────────────────


@router.get("/api/holdings")
async def get_holdings_api(
    repo: Repository = Depends(get_repository),
) -> dict[str, Any]:
    snapshots = await repo.get_latest_holdings()
    history = await repo.get_cash_balance_history(limit=1)
    portfolio_val = history[0].total_cash if history else 100000.0

    holdings_list = []
    for s in snapshots:
        cur_price = s.current_price or s.avg_price
        val = s.quantity * cur_price
        pnl = (cur_price - s.avg_price) * s.quantity
        pnl_pct = (
            ((cur_price - s.avg_price) / s.avg_price * 100) if s.avg_price > 0 else 0.0
        )
        alloc_pct = (val / portfolio_val * 100) if portfolio_val > 0 else 0.0

        holdings_list.append(
            {
                "symbol": s.symbol,
                "quantity": s.quantity,
                "average_price": s.avg_price,
                "current_price": cur_price,
                "current_value": val,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "allocation_pct": alloc_pct,
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
            }
        )

    return {"holdings": holdings_list, "portfolio_value": portfolio_val}


@router.get("/api/orders")
async def get_orders_api(repo: Repository = Depends(get_repository)) -> dict[str, Any]:
    orders = await repo.get_recent_orders(limit=100)
    open_orders = []
    completed_orders = []

    for o in orders:
        data = {
            "id": o.id,
            "symbol": o.symbol,
            "side": o.side,
            "order_type": o.order_type,
            "quantity": o.quantity,
            "price": o.price,
            "status": o.status,
            "filled_quantity": o.filled_quantity,
            "filled_price": o.filled_price,
            "placed_at": o.placed_at.isoformat() if o.placed_at else None,
            "completed_at": o.completed_at.isoformat() if o.completed_at else None,
            "error_message": o.error_message,
        }
        if o.status in ("PENDING", "OPEN", "PARTIALLY_FILLED"):
            open_orders.append(data)
        else:
            completed_orders.append(data)

    return {"open_orders": open_orders, "completed_orders": completed_orders}


@router.get("/api/balance")
async def get_balance_api(repo: Repository = Depends(get_repository)) -> dict[str, Any]:
    history = await repo.get_cash_balance_history(limit=1)
    if history:
        rec = history[0]
        return {
            "available_cash": rec.available_cash,
            "used_cash": rec.used_cash,
            "total_cash": rec.total_cash,
            "timestamp": rec.timestamp.isoformat() if rec.timestamp else None,
        }
    return {
        "available_cash": 100000.0,
        "used_cash": 0.0,
        "total_cash": 100000.0,
        "timestamp": None,
    }


def _get_rules_config() -> tuple[Any, str]:
    rules_path = Path("config/rules.yaml")
    raw_yaml = ""
    if rules_path.exists():
        raw_yaml = rules_path.read_text(encoding="utf-8")
        cfg = load_rules(rules_path)
    else:
        from portfolio_automation.rules.models import RulesConfig

        cfg = RulesConfig()
    return cfg, raw_yaml


@router.get("/api/buy-queue")
async def get_buy_queue_api() -> dict[str, Any]:
    cfg, raw_yaml = _get_rules_config()
    queue = [
        {
            "symbol": entry.symbol,
            "priority": entry.priority,
            "condition": entry.condition.model_dump(),
        }
        for entry in cfg.buy_queue
    ]
    return {"buy_queue": queue, "raw_yaml": raw_yaml}


@router.get("/api/sell-rules")
async def get_sell_rules_api() -> dict[str, Any]:
    cfg, raw_yaml = _get_rules_config()
    rules = [
        {
            "symbol": rule.symbol,
            "condition": rule.condition.model_dump(),
        }
        for rule in cfg.sell_rules
    ]
    return {"sell_rules": rules, "raw_yaml": raw_yaml}


@router.get("/api/logs")
async def get_logs_api(repo: Repository = Depends(get_repository)) -> dict[str, Any]:
    logs = await repo.get_recent_logs(limit=50)
    entries = [
        {
            "id": log.id,
            "level": log.level,
            "module": log.module,
            "message": log.message,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        }
        for log in logs
    ]
    return {"logs": entries}


# ── Settings Mutation API ───────────────────────────────────────────────────


class RulesUpdateRequest(BaseModel):
    yaml_content: str


@router.post("/api/settings/rules")
async def update_rules_api(request: RulesUpdateRequest) -> dict[str, Any]:
    try:
        save_rules_from_string(request.yaml_content, path="config/rules.yaml")
        websocket_manager.broadcast(
            "RULES_UPDATED", {"message": "Rules updated via YAML"}
        )
        return {"status": "success", "message": "Rules updated successfully"}
    except RuleLoadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save rules: {e}")


class ToggleRequest(BaseModel):
    enabled: bool


@router.post("/api/settings/automation")
async def toggle_automation_api(
    request: Request, body: ToggleRequest, repo: Repository = Depends(get_repository)
) -> dict[str, Any]:
    request.app.state.automation_enabled = body.enabled
    await repo.set_config("automation_enabled", str(body.enabled).lower())
    websocket_manager.broadcast(
        "AUTOMATION_TOGGLED",
        {"enabled": body.enabled, "message": f"Automation enabled: {body.enabled}"},
    )
    return {"status": "success", "enabled": body.enabled}


@router.post("/api/settings/dry-run")
async def toggle_dry_run_api(
    request: Request, body: ToggleRequest, repo: Repository = Depends(get_repository)
) -> dict[str, Any]:
    request.app.state.config.safety.dry_run = body.enabled
    if hasattr(request.app.state, "safety_gate") and request.app.state.safety_gate:
        request.app.state.safety_gate.dry_run_guard.enabled = body.enabled
    await repo.set_config("dry_run", str(body.enabled).lower())
    websocket_manager.broadcast(
        "DRY_RUN_TOGGLED",
        {"enabled": body.enabled, "message": f"Dry-run mode: {body.enabled}"},
    )
    return {"status": "success", "enabled": body.enabled}


class KillSwitchRequest(BaseModel):
    active: bool = True


@router.post("/api/kill-switch", dependencies=[Depends(verify_api_key)])
async def trigger_kill_switch_api(
    request: Request, body: KillSwitchRequest | None = None
) -> dict[str, Any]:
    active = body.active if body is not None else True
    gate = getattr(request.app.state, "safety_gate", None)
    if gate:
        if active:
            gate.kill_switch.activate()
        else:
            gate.kill_switch.deactivate()

    msg = "Kill switch activated" if active else "Kill switch deactivated"
    websocket_manager.broadcast(
        EventType.SAFETY_TRIGGERED, {"active": active, "message": msg, "side": "SYSTEM"}
    )
    return {"status": "success", "active": active, "message": msg}


class ManualConfirmationRequest(BaseModel):
    action: str  # "approve" or "reject"


@router.post(
    "/api/manual-confirmation/{symbol}", dependencies=[Depends(verify_api_key)]
)
async def process_manual_confirmation_api(
    request: Request, symbol: str, body: ManualConfirmationRequest
) -> dict[str, Any]:
    gate = getattr(request.app.state, "safety_gate", None)
    if not gate:
        raise HTTPException(status_code=500, detail="SafetyGate not available")

    symbol = symbol.upper()
    if body.action.lower() == "approve":
        gate.manual_confirmation.approve(symbol)
        msg = f"Trade approved for {symbol}"
    elif body.action.lower() == "reject":
        gate.manual_confirmation.unflag_symbol(symbol)
        msg = f"Trade rejected for {symbol}"
    else:
        raise HTTPException(
            status_code=400, detail="Action must be 'approve' or 'reject'"
        )

    websocket_manager.broadcast(
        "MANUAL_CONFIRMATION", {"symbol": symbol, "action": body.action, "message": msg}
    )
    return {"status": "success", "symbol": symbol, "action": body.action}


# ── WebSocket Route ─────────────────────────────────────────────────────────


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)
