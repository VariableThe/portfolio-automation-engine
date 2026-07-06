from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

MANUAL_CONFIRM_TIMEOUT = 300.0


class ManualConfirmation:
    def __init__(self) -> None:
        self._flagged: dict[str, asyncio.Event] = {}

    def flag_symbol(self, symbol: str) -> None:
        if symbol not in self._flagged:
            self._flagged[symbol] = asyncio.Event()
            logger.info("Manual confirmation required for %s", symbol)

    def unflag_symbol(self, symbol: str) -> None:
        self._flagged.pop(symbol, None)
        logger.info("Manual confirmation removed for %s", symbol)

    def approve(self, symbol: str) -> None:
        event = self._flagged.get(symbol)
        if event is not None:
            event.set()
            logger.info("Manual approval granted for %s", symbol)

    def is_flagged(self, symbol: str) -> bool:
        return symbol in self._flagged

    def flagged_symbols(self) -> set[str]:
        return set(self._flagged.keys())

    async def wait_for_approval(
        self, symbol: str, timeout: float = MANUAL_CONFIRM_TIMEOUT
    ) -> str | None:
        event = self._flagged.get(symbol)
        if event is None:
            return None
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return None
        except TimeoutError:
            return f"Manual confirmation timeout for {symbol} " f"after {timeout:.0f}s"
