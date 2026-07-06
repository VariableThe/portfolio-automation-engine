from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from portfolio_automation.core.config import LoggingConfig
from portfolio_automation.logger.setup import setup_logging
from portfolio_automation.main import async_main, main


def test_setup_logging(tmp_path):
    log_file = tmp_path / "test.log"
    cfg_console = LoggingConfig(
        level="DEBUG", format="console", file_path=str(log_file)
    )
    setup_logging(cfg_console)
    assert log_file.exists()

    cfg_json = LoggingConfig(level="INFO", format="json", file_path=None)
    setup_logging(cfg_json)


@pytest.mark.asyncio
async def test_async_main():
    with patch("portfolio_automation.main.load_config") as mock_load:
        mock_cfg = MagicMock()
        mock_cfg.broker.type = "mock"
        mock_cfg.safety.dry_run = True
        mock_cfg.execution.poll_interval_seconds = 1
        mock_cfg.execution.market_open = "09:15"
        mock_cfg.execution.market_close = "15:30"
        mock_load.return_value = mock_cfg

        with patch(
            "portfolio_automation.main.asyncio.sleep",
            side_effect=asyncio.CancelledError,
        ):
            await async_main()


def test_main():
    with patch("portfolio_automation.main.load_config") as mock_load:
        mock_cfg = MagicMock()
        mock_cfg.broker.type = "mock"
        mock_cfg.safety.dry_run = True
        mock_cfg.logging = LoggingConfig()
        mock_load.return_value = mock_cfg

        with patch("portfolio_automation.main.async_main", new=MagicMock()), patch(
            "portfolio_automation.main.asyncio.run", side_effect=KeyboardInterrupt
        ):
            main()
