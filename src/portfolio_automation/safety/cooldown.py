from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from portfolio_automation.persistence.repository import Repository

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_HOURS = 24


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class StockCooldownManager:
    def __init__(
        self,
        repository: Repository,
        cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
    ) -> None:
        self._repo = repository
        self._cooldown_hours = cooldown_hours

    async def start_cooldown(self, symbol: str, sell_order_id: str) -> None:
        cooldown_until = _now() + timedelta(hours=self._cooldown_hours)
        await self._repo.save_cooldown(symbol, sell_order_id, cooldown_until)
        logger.info(
            "Cooldown started for %s until %s (order=%s)",
            symbol,
            cooldown_until.isoformat(),
            sell_order_id,
        )

    async def check_cooldown(self, symbol: str) -> str | None:
        cooldown = await self._repo.get_active_cooldown(symbol)
        if cooldown is not None:
            remaining = cooldown.cooldown_until - _now()
            hours = remaining.total_seconds() / 3600
            return (
                f"Cooldown active for {symbol}: "
                f"{hours:.1f}h remaining (until {cooldown.cooldown_until.isoformat()})"
            )
        return None

    async def get_all_active_cooldowns(self) -> list[dict[str, object]]:
        records = await self._repo.get_all_active_cooldowns()
        return [
            {
                "symbol": r.symbol,
                "cooldown_until": r.cooldown_until.isoformat(),
                "sell_order_id": r.sell_order_id,
            }
            for r in records
        ]
