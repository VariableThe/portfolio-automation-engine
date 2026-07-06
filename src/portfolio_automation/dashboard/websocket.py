from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

from portfolio_automation.dashboard.broadcaster import DashboardBroadcaster

logger = logging.getLogger(__name__)


class WebSocketManager(DashboardBroadcaster):
    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()
        self.event_queue: asyncio.Queue[dict[str, Any]] | None = None
        self._dispatch_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _ensure_queue(self) -> asyncio.Queue[dict[str, Any]]:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self.event_queue is None or (current_loop and self._loop != current_loop):
            self.event_queue = asyncio.Queue()
            self._loop = current_loop
            if self._dispatch_task and not self._dispatch_task.done():
                self._dispatch_task.cancel()
            self._dispatch_task = None
        return self.event_queue

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(
            "WebSocket connected. Active clients: %d", len(self.active_connections)
        )
        self.start_dispatch()

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)
        logger.info(
            "WebSocket disconnected. Active clients: %d", len(self.active_connections)
        )

    def start_dispatch(self) -> None:
        q = self._ensure_queue()
        if self._dispatch_task is None or self._dispatch_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._dispatch_task = loop.create_task(self._dispatch_loop(q))
            except RuntimeError:
                pass

    async def stop_dispatch(self) -> None:
        if self._dispatch_task and not self._dispatch_task.done():
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
            self._dispatch_task = None

    async def _dispatch_loop(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        while True:
            try:
                event = await q.get()
                await self._send_to_all(event)
                q.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in WebSocket dispatch loop: %s", e)

    async def _send_to_all(self, event: dict[str, Any]) -> None:
        if not self.active_connections:
            return
        to_remove = set()
        for ws in list(self.active_connections):
            try:
                await ws.send_json(event)
            except Exception:
                to_remove.add(ws)
        for ws in to_remove:
            self.disconnect(ws)

    def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Non-blocking fire-and-forget push to all connected WebSocket clients."""
        payload = {"type": event_type, "data": data}
        try:
            q = self._ensure_queue()
            q.put_nowait(payload)
            self.start_dispatch()
        except Exception as e:
            logger.debug("Failed to queue WebSocket event: %s", e)


websocket_manager = WebSocketManager()
