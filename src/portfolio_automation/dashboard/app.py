from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from portfolio_automation.core.config import ConfigModel, load_config
from portfolio_automation.dashboard.websocket import websocket_manager
from portfolio_automation.persistence.database import (
    create_engine,
    create_session_factory,
)
from portfolio_automation.safety.dry_run import DryRunGuard
from portfolio_automation.safety.gate import SafetyGate

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    logger.info("Starting up FastAPI dashboard...")
    if not hasattr(app.state, "config") or app.state.config is None:
        app.state.config = load_config()

    if not hasattr(app.state, "session_factory") or app.state.session_factory is None:
        engine = create_engine(app.state.config)
        async with engine.begin() as conn:
            from portfolio_automation.persistence.models import Base

            await conn.run_sync(Base.metadata.create_all)
        app.state.db_engine = engine
        app.state.session_factory = create_session_factory(engine)
    else:
        app.state.db_engine = None

    if not hasattr(app.state, "safety_gate") or app.state.safety_gate is None:
        async with app.state.session_factory() as session:
            from portfolio_automation.persistence.repository import Repository

            repo = Repository(session)
            app.state.safety_gate = SafetyGate(
                repository=repo,
                dry_run_guard=DryRunGuard(enabled=app.state.config.safety.dry_run),
            )

    if not hasattr(app.state, "automation_enabled"):
        app.state.automation_enabled = True

    websocket_manager.start_dispatch()

    yield

    logger.info("Shutting down FastAPI dashboard...")
    await websocket_manager.stop_dispatch()
    if getattr(app.state, "db_engine", None) is not None:
        await app.state.db_engine.dispose()


def create_app(config: ConfigModel | None = None) -> FastAPI:
    app = FastAPI(
        title="Portfolio Automation Engine Dashboard",
        version="0.1.0",
        lifespan=lifespan,
    )

    if config is not None:
        app.state.config = config

    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from portfolio_automation.dashboard.routes import router

    app.include_router(router)

    return app


app = create_app()
