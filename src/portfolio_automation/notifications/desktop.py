from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class DesktopChannel:
    def __init__(
        self,
        app_name: str = "Portfolio Automation",
        notifier_func: Callable[..., Any] | None = None,
    ) -> None:
        self.app_name = app_name
        self._notifier_func = notifier_func

    async def send(self, event_type: str, message: str, data: dict[str, Any]) -> None:
        """Send a short desktop notification using plyer."""
        title = f"[{event_type}] {self.app_name}"
        lines = message.splitlines()
        body_lines = [
            line
            for line in lines
            if not any(
                line.startswith(e) for e in ("🟢", "🔴", "⚠️", "🛡️", "❌", "📊", "📋")
            )
        ]
        body = "\n".join(body_lines[:3]) if body_lines else message[:200]
        if len(body) > 200:
            body = body[:197] + "..."

        func = self._notifier_func
        if func is None:
            try:
                from plyer import notification

                func = notification.notify
            except ImportError:
                logger.warning("plyer not installed, desktop notifications unavailable")
                return

        def _sync_notify() -> None:
            func(
                title=title,
                message=body,
                app_name=self.app_name,
                timeout=5,
            )

        try:
            await asyncio.to_thread(_sync_notify)
        except Exception as e:
            logger.debug("Desktop notification display failed: %s", e)
            raise
