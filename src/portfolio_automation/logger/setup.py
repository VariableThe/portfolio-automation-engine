from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog

from portfolio_automation.core.config import LoggingConfig


def setup_logging(config: LoggingConfig) -> None:
    level = getattr(logging, config.level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        (
            structlog.dev.ConsoleRenderer()
            if config.format == "console"
            else structlog.processors.JSONRenderer()
        ),
    ]

    if config.file_path:
        log_path = Path(config.file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(log_path))
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logging.getLogger().addHandler(handler)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
