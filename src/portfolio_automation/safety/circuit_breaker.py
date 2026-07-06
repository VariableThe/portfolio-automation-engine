from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class KillSwitch:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def is_active(self) -> bool:
        return self._event.is_set()

    def activate(self) -> None:
        if not self._event.is_set():
            self._event.set()
            logger.warning("Kill switch ACTIVATED — no new orders will be placed")

    def deactivate(self) -> None:
        if self._event.is_set():
            self._event.clear()
            logger.info("Kill switch DEACTIVATED — order placement resumed")

    async def wait_if_active(self) -> None:
        if self._event.is_set():
            logger.warning("Waiting for kill switch to be deactivated")
            await self._event.wait()
