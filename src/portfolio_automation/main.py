from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from portfolio_automation.core.config import load_config
from portfolio_automation.logger.setup import setup_logging

logger = structlog.get_logger(__name__)


async def async_main() -> None:
    config_dir = Path("config")
    config = load_config(config_dir)

    logger.info(
        "engine_initialized",
        broker_type=config.broker.type,
        dry_run=config.safety.dry_run,
        poll_interval=config.execution.poll_interval_seconds,
        market_open=config.execution.market_open,
        market_close=config.execution.market_close,
    )

    while True:
        try:
            logger.debug("cycle_start")
            await asyncio.sleep(config.execution.poll_interval_seconds)
        except asyncio.CancelledError:
            logger.info("engine_stopped")
            break


def main() -> None:
    config_dir = Path("config")
    config = load_config(config_dir)
    setup_logging(config.logging)

    logger.info(
        "portfolio_automation_starting",
        broker_type=config.broker.type,
        dry_run=config.safety.dry_run,
    )

    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("portfolio_automation_shutdown", reason="keyboard_interrupt")


if __name__ == "__main__":
    main()
