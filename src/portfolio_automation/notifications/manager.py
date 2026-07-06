from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from portfolio_automation.core.config import NotificationConfig
from portfolio_automation.notifications.console import ConsoleChannel
from portfolio_automation.notifications.desktop import DesktopChannel
from portfolio_automation.notifications.discord import DiscordChannel
from portfolio_automation.notifications.formatting import format_message
from portfolio_automation.notifications.interface import EventType, NotificationChannel
from portfolio_automation.notifications.telegram import TelegramChannel

logger = logging.getLogger(__name__)


class NotificationManager:
    def __init__(
        self,
        channels: dict[str, NotificationChannel] | None = None,
        throttle_seconds: float = 60.0,
    ) -> None:
        self.channels: dict[str, NotificationChannel] = channels or {}
        self.throttle_seconds = throttle_seconds
        self._last_notified: dict[tuple[str, str | None, str | None], float] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()

    @classmethod
    def from_config(cls, config: NotificationConfig) -> NotificationManager:
        manager = cls()
        if not getattr(config, "enabled", True):
            return manager

        channels_cfg = getattr(config, "channels", None)
        if channels_cfg:
            if channels_cfg.console.enabled:
                manager.register_channel(
                    "console", ConsoleChannel(level=channels_cfg.console.level)
                )
            if channels_cfg.desktop.enabled:
                manager.register_channel("desktop", DesktopChannel())
            if channels_cfg.discord.enabled and channels_cfg.discord.webhook_url:
                manager.register_channel(
                    "discord",
                    DiscordChannel(webhook_url=channels_cfg.discord.webhook_url),
                )
            if (
                channels_cfg.telegram.enabled
                and channels_cfg.telegram.bot_token
                and channels_cfg.telegram.chat_id
            ):
                manager.register_channel(
                    "telegram",
                    TelegramChannel(
                        bot_token=channels_cfg.telegram.bot_token,
                        chat_id=channels_cfg.telegram.chat_id,
                    ),
                )
        return manager

    def register_channel(self, name: str, channel: NotificationChannel) -> None:
        self.channels[name] = channel

    def unregister_channel(self, name: str) -> None:
        self.channels.pop(name, None)

    def _is_throttled(self, event_type: str, data: dict[str, Any]) -> bool:
        if self.throttle_seconds <= 0:
            return False
        now = time.time()
        symbol = data.get("symbol")
        order_id = (
            data.get("order_id", data.get("id"))
            if event_type in (EventType.ORDER_PLACED, EventType.ORDER_FILLED)
            else None
        )
        key = (event_type, symbol, order_id)
        last = self._last_notified.get(key)
        if last is not None and (now - last) < self.throttle_seconds:
            return True
        self._last_notified[key] = now
        return False

    def reset_throttling(self) -> None:
        self._last_notified.clear()

    async def notify(
        self,
        event_type: str,
        data: dict[str, Any],
        message: str | None = None,
    ) -> None:
        """Dispatch notification to all configured channels asynchronously."""
        if not self.channels:
            return

        try:
            if self._is_throttled(event_type, data):
                logger.debug(
                    "Notification throttled: event_type=%s, symbol=%s",
                    event_type,
                    data.get("symbol"),
                )
                return

            formatted_message = message or format_message(event_type, data)

            names = list(self.channels.keys())
            tasks = [
                self._send_to_channel(name, chan, event_type, formatted_message, data)
                for name, chan in self.channels.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for name, res in zip(names, results):
                if isinstance(res, Exception):
                    logger.error(
                        "Notification channel '%s' failed for event '%s': %s",
                        name,
                        event_type,
                        res,
                    )
        except Exception as e:
            logger.exception("Unexpected error in NotificationManager.notify: %s", e)

    async def _send_to_channel(
        self,
        name: str,
        channel: NotificationChannel,
        event_type: str,
        message: str,
        data: dict[str, Any],
    ) -> None:
        await channel.send(event_type, message, data)

    def fire_and_forget(
        self,
        event_type: str,
        data: dict[str, Any],
        message: str | None = None,
    ) -> asyncio.Task[None] | None:
        """Schedule notification as a background task without blocking the caller."""
        if not self.channels:
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None

        task = loop.create_task(self.notify(event_type, data, message))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def wait_all(self, timeout: float = 5.0) -> None:
        """Wait for any pending background tasks to finish."""
        if self._background_tasks:
            await asyncio.wait(list(self._background_tasks), timeout=timeout)
