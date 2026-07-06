from __future__ import annotations

import collections
import html
import logging
import time
from typing import Any

import aiohttp

from portfolio_automation.notifications.interface import RateLimitExceededError

logger = logging.getLogger(__name__)


class TelegramChannel:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        session: aiohttp.ClientSession | None = None,
        max_global_per_second: int = 30,
        min_chat_interval: float = 1.0,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._session = session
        self._max_global_per_second = max_global_per_second
        self._min_chat_interval = min_chat_interval
        self._global_timestamps: collections.deque[float] = collections.deque()
        self._last_chat_sent: dict[str, float] = {}

    def _check_rate_limit(self, target_chat_id: str) -> None:
        now = time.time()
        while self._global_timestamps and (now - self._global_timestamps[0]) >= 1.0:
            self._global_timestamps.popleft()
        if len(self._global_timestamps) >= self._max_global_per_second:
            raise RateLimitExceededError(
                f"Telegram global rate limit exceeded ({self._max_global_per_second} msg/sec)"
            )

        last_sent = self._last_chat_sent.get(target_chat_id, 0.0)
        if (now - last_sent) < self._min_chat_interval:
            raise RateLimitExceededError(
                f"Telegram chat rate limit exceeded for {target_chat_id} (1 msg/sec)"
            )

        self._global_timestamps.append(now)
        self._last_chat_sent[target_chat_id] = now

    def _format_html(self, message: str) -> str:
        lines = message.splitlines()
        if not lines:
            return html.escape(message)
        title = lines[0]
        formatted_lines = [f"<b>{html.escape(title)}</b>"]
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                if "Order ID" in k:
                    formatted_lines.append(
                        f"<b>{html.escape(k.strip())}:</b> <code>{html.escape(v.strip())}</code>"
                    )
                else:
                    formatted_lines.append(
                        f"<b>{html.escape(k.strip())}:</b>{html.escape(v)}"
                    )
            else:
                formatted_lines.append(html.escape(line))
        return "\n".join(formatted_lines)

    async def send(self, event_type: str, message: str, data: dict[str, Any]) -> None:
        """Send an HTML-formatted notification to Telegram Bot API."""
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram bot_token or chat_id not configured")
            return

        target_chat_id = str(data.get("chat_id", self.chat_id))
        self._check_rate_limit(target_chat_id)

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": target_chat_id,
            "text": self._format_html(message),
            "parse_mode": "HTML",
        }

        timeout = aiohttp.ClientTimeout(total=10)
        session = self._session
        if session is None or session.closed:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, json=payload, timeout=timeout) as resp:
                    resp.raise_for_status()
        else:
            async with session.post(url, json=payload, timeout=timeout) as resp:
                resp.raise_for_status()
