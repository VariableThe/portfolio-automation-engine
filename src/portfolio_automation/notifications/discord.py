from __future__ import annotations

import collections
import logging
import time
from typing import Any

import aiohttp

from portfolio_automation.notifications.interface import (
    EventType,
    RateLimitExceededError,
)

logger = logging.getLogger(__name__)


class DiscordChannel:
    def __init__(
        self,
        webhook_url: str,
        session: aiohttp.ClientSession | None = None,
        max_requests: int = 30,
        window_seconds: float = 60.0,
    ) -> None:
        self.webhook_url = webhook_url
        self._session = session
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._request_timestamps: collections.deque[float] = collections.deque()

    def _check_rate_limit(self) -> None:
        now = time.time()
        while (
            self._request_timestamps
            and (now - self._request_timestamps[0]) >= self._window_seconds
        ):
            self._request_timestamps.popleft()
        if len(self._request_timestamps) >= self._max_requests:
            raise RateLimitExceededError(
                f"Discord rate limit exceeded ({self._max_requests} req/{self._window_seconds}s)"
            )
        self._request_timestamps.append(now)

    def _get_embed_color(self, event_type: str, data: dict[str, Any]) -> int:
        side = str(data.get("side", "")).upper()
        if event_type in (EventType.ORDER_FILLED, EventType.ORDER_PLACED):
            if side == "BUY":
                return 0x2ECC71
            elif side == "SELL":
                return 0xE74C3C
            return 0x3498DB
        if event_type in (EventType.ORDER_REJECTED, EventType.SAFETY_TRIGGERED):
            return 0xF1C40F
        if event_type == EventType.ERROR:
            return 0xE74C3C
        return 0x3498DB

    async def send(self, event_type: str, message: str, data: dict[str, Any]) -> None:
        """Send a rich embed notification to Discord via webhook."""
        if not self.webhook_url:
            logger.warning("Discord webhook_url not configured")
            return

        self._check_rate_limit()

        lines = message.splitlines()
        title = lines[0] if lines else event_type
        desc_lines = lines[1:] if len(lines) > 1 else []

        formatted_desc_lines = []
        for line in desc_lines:
            if ":" in line:
                k, v = line.split(":", 1)
                if "Order ID" in k:
                    formatted_desc_lines.append(f"**{k.strip()}**: `{v.strip()}`")
                else:
                    formatted_desc_lines.append(f"**{k.strip()}**:{v}")
            else:
                formatted_desc_lines.append(line)

        description = (
            "\n".join(formatted_desc_lines) if formatted_desc_lines else message
        )

        embed = {
            "title": title,
            "description": description,
            "color": self._get_embed_color(event_type, data),
        }
        payload = {"embeds": [embed]}

        session = self._session
        if session is None or session.closed:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    self.webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resp.raise_for_status()
        else:
            async with session.post(
                self.webhook_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
