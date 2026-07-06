from __future__ import annotations

from typing import Any, Protocol


class DashboardBroadcaster(Protocol):
    def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Broadcast an event non-blockingly to all connected dashboard clients."""
        ...


class DashboardNotificationChannel:
    """Wrapper to register a DashboardBroadcaster inside NotificationManager."""

    def __init__(self, broadcaster: DashboardBroadcaster) -> None:
        self.broadcaster = broadcaster

    async def send(self, event_type: str, message: str, data: dict[str, Any]) -> None:
        payload = dict(data)
        if message and "message" not in payload:
            payload["message"] = message
        self.broadcaster.broadcast(event_type, payload)
