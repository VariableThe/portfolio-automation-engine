from __future__ import annotations

from typing import Any

from rich.console import Console

from portfolio_automation.notifications.interface import EventType


class ConsoleChannel:
    def __init__(self, console: Console | None = None, level: str = "INFO") -> None:
        self.console = console or Console()
        self.level = level.upper()

    async def send(self, event_type: str, message: str, data: dict[str, Any]) -> None:
        """Send a notification formatted to the console with rich colors."""
        side = str(data.get("side", "")).upper()
        if event_type in (EventType.ORDER_FILLED, EventType.ORDER_PLACED):
            if side == "BUY":
                style = "bold green"
            elif side == "SELL":
                style = "bold red"
            else:
                style = "bold cyan"
        elif event_type in (EventType.ORDER_REJECTED, EventType.SAFETY_TRIGGERED):
            style = "bold yellow"
        elif event_type == EventType.ERROR:
            style = "bold red"
        else:
            style = "bold blue"

        self.console.print(message, style=style)
