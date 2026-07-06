from __future__ import annotations

from typing import Any, Protocol


class EventType:
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    SAFETY_TRIGGERED = "SAFETY_TRIGGERED"
    ERROR = "ERROR"
    DAILY_SUMMARY = "DAILY_SUMMARY"


class RateLimitExceededError(Exception):
    """Raised when a notification channel exceeds its rate limit."""


class NotificationChannel(Protocol):
    async def send(self, event_type: str, message: str, data: dict[str, Any]) -> None:
        """Send a notification asynchronously and non-blockingly."""
        ...
