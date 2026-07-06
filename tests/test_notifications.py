from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from portfolio_automation.core.config import NotificationConfig
from portfolio_automation.notifications.console import ConsoleChannel
from portfolio_automation.notifications.desktop import DesktopChannel
from portfolio_automation.notifications.discord import DiscordChannel
from portfolio_automation.notifications.formatting import format_message
from portfolio_automation.notifications.interface import (
    EventType,
    RateLimitExceededError,
)
from portfolio_automation.notifications.manager import NotificationManager
from portfolio_automation.notifications.telegram import TelegramChannel


class MockChannel:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, dict]] = []

    async def send(self, event_type: str, message: str, data: dict) -> None:
        self.calls.append((event_type, message, data))
        if self.fail:
            raise RuntimeError("Intentional channel failure")


@pytest.mark.asyncio
async def test_notification_manager_dispatch_and_failure_isolation() -> None:
    chan_success = MockChannel(fail=False)
    chan_fail = MockChannel(fail=True)
    chan_success_2 = MockChannel(fail=False)

    manager = NotificationManager(
        channels={
            "s1": chan_success,
            "f1": chan_fail,
            "s2": chan_success_2,
        },
        throttle_seconds=0,
    )

    await manager.notify(
        EventType.ORDER_PLACED,
        {"symbol": "RELIANCE", "side": "BUY", "quantity": 10, "price": 2500.0},
    )

    assert len(chan_success.calls) == 1
    assert len(chan_fail.calls) == 1
    assert len(chan_success_2.calls) == 1
    assert chan_success.calls[0][0] == EventType.ORDER_PLACED


@pytest.mark.asyncio
async def test_notification_manager_throttling() -> None:
    chan = MockChannel()
    manager = NotificationManager(channels={"test": chan}, throttle_seconds=60.0)

    # First event should pass
    await manager.notify(EventType.SAFETY_TRIGGERED, {"symbol": "TCS", "side": "BUY"})
    assert len(chan.calls) == 1

    # Second event for same symbol/event_type within 60s should be throttled
    await manager.notify(EventType.SAFETY_TRIGGERED, {"symbol": "TCS", "side": "BUY"})
    assert len(chan.calls) == 1

    # Different symbol should pass
    await manager.notify(EventType.SAFETY_TRIGGERED, {"symbol": "INFY", "side": "BUY"})
    assert len(chan.calls) == 2

    # Different event type should pass
    await manager.notify(EventType.ORDER_REJECTED, {"symbol": "TCS", "side": "BUY"})
    assert len(chan.calls) == 3


@pytest.mark.asyncio
async def test_fire_and_forget() -> None:
    chan = MockChannel()
    manager = NotificationManager(channels={"test": chan}, throttle_seconds=0)

    task = manager.fire_and_forget(EventType.DAILY_SUMMARY, {"symbol": "ALL"})
    assert task is not None
    await manager.wait_all()
    assert len(chan.calls) == 1


def test_format_message() -> None:
    msg = format_message(
        EventType.ORDER_FILLED,
        {
            "symbol": "HDFC",
            "side": "BUY",
            "quantity": 50,
            "price": 1500.0,
            "order_id": "ord-123",
        },
    )
    assert "BUY" in msg
    assert "HDFC" in msg
    assert "1,500.00" in msg


@pytest.mark.asyncio
async def test_console_channel() -> None:
    mock_console = MagicMock()
    chan = ConsoleChannel(console=mock_console)
    await chan.send(EventType.ORDER_PLACED, "Placed BUY order", {"side": "BUY"})
    mock_console.print.assert_called_once()
    assert mock_console.print.call_args[1]["style"] == "bold green"


@pytest.mark.asyncio
async def test_desktop_channel() -> None:
    mock_notifier = MagicMock()
    chan = DesktopChannel(notifier_func=mock_notifier)
    await chan.send(EventType.SAFETY_TRIGGERED, "Safety triggered for RELIANCE", {})
    mock_notifier.assert_called_once()
    assert "[SAFETY_TRIGGERED]" in mock_notifier.call_args[1]["title"]


@pytest.mark.asyncio
async def test_discord_channel_rate_limit() -> None:
    chan = DiscordChannel(
        webhook_url="https://discord.com/api/webhooks/test",
        max_requests=2,
        window_seconds=60,
    )
    mock_session = MagicMock()
    mock_session.closed = False
    mock_resp = MagicMock()
    mock_session.post.return_value.__aenter__.return_value = mock_resp
    chan._session = mock_session

    await chan.send(EventType.ORDER_PLACED, "Msg 1", {"side": "BUY"})
    await chan.send(EventType.ORDER_PLACED, "Msg 2", {"side": "BUY"})

    with pytest.raises(RateLimitExceededError):
        await chan.send(EventType.ORDER_PLACED, "Msg 3", {"side": "BUY"})


@pytest.mark.asyncio
async def test_telegram_channel_rate_limit() -> None:
    chan = TelegramChannel(
        bot_token="token",
        chat_id="12345",
        max_global_per_second=2,
        min_chat_interval=10.0,
    )
    mock_session = MagicMock()
    mock_session.closed = False
    mock_resp = MagicMock()
    mock_session.post.return_value.__aenter__.return_value = mock_resp
    chan._session = mock_session

    await chan.send(EventType.ORDER_PLACED, "Msg 1", {"chat_id": "chat1"})
    with pytest.raises(RateLimitExceededError):
        await chan.send(EventType.ORDER_PLACED, "Msg 2", {"chat_id": "chat1"})


def test_from_config() -> None:
    cfg = NotificationConfig()
    cfg.channels.console.enabled = True
    cfg.channels.desktop.enabled = True
    cfg.channels.discord.enabled = True
    cfg.channels.discord.webhook_url = "https://discord.com/hook"
    cfg.channels.telegram.enabled = True
    cfg.channels.telegram.bot_token = "bot"
    cfg.channels.telegram.chat_id = "chat"

    manager = NotificationManager.from_config(cfg)
    assert "console" in manager.channels
    assert "desktop" in manager.channels
    assert "discord" in manager.channels
    assert "telegram" in manager.channels
