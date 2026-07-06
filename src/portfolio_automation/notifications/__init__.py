from __future__ import annotations

from portfolio_automation.notifications.console import ConsoleChannel
from portfolio_automation.notifications.desktop import DesktopChannel
from portfolio_automation.notifications.discord import DiscordChannel
from portfolio_automation.notifications.formatting import format_message
from portfolio_automation.notifications.interface import (
    EventType,
    NotificationChannel,
    RateLimitExceededError,
)
from portfolio_automation.notifications.manager import NotificationManager
from portfolio_automation.notifications.telegram import TelegramChannel

__all__ = [
    "ConsoleChannel",
    "DesktopChannel",
    "DiscordChannel",
    "EventType",
    "NotificationChannel",
    "NotificationManager",
    "RateLimitExceededError",
    "TelegramChannel",
    "format_message",
]
