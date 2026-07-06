from __future__ import annotations

import logging
from dataclasses import dataclass

from portfolio_automation.persistence.repository import Repository
from portfolio_automation.safety.circuit_breaker import KillSwitch
from portfolio_automation.safety.cooldown import StockCooldownManager
from portfolio_automation.safety.dry_run import DryRunGuard
from portfolio_automation.safety.limits import DailyLimits
from portfolio_automation.safety.manual_confirmation import (
    ManualConfirmation,
)

logger = logging.getLogger(__name__)


@dataclass
class SafetyCheckResult:
    passed: bool = True
    reason: str = ""
    is_dry_run: bool = False


class SafetyGate:
    def __init__(
        self,
        repository: Repository,
        kill_switch: KillSwitch | None = None,
        cooldown_manager: StockCooldownManager | None = None,
        daily_limits: DailyLimits | None = None,
        dry_run_guard: DryRunGuard | None = None,
        manual_confirmation: ManualConfirmation | None = None,
    ) -> None:
        self._repo = repository
        self._kill_switch = kill_switch or KillSwitch()
        self._cooldown = cooldown_manager or StockCooldownManager(repository)
        self._limits = daily_limits or DailyLimits(repository)
        self._dry_run = dry_run_guard or DryRunGuard()
        self._manual = manual_confirmation or ManualConfirmation()

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    @property
    def dry_run_guard(self) -> DryRunGuard:
        return self._dry_run

    @property
    def manual_confirmation(self) -> ManualConfirmation:
        return self._manual

    @property
    def cooldown_manager(self) -> StockCooldownManager:
        return self._cooldown

    async def can_buy(
        self,
        symbol: str,
        quantity: int,
        price: float,
        portfolio_value: float | None = None,
    ) -> SafetyCheckResult:
        if self._kill_switch.is_active():
            return SafetyCheckResult(
                passed=False,
                reason="Kill switch is active — no orders permitted",
            )

        reason = self._dry_run.check()
        if reason is not None:
            return SafetyCheckResult(passed=True, reason=reason, is_dry_run=True)

        reason = await self._cooldown.check_cooldown(symbol)
        if reason is not None:
            return SafetyCheckResult(passed=False, reason=reason)

        reason = await self._limits.check_trade_count()
        if reason is not None:
            return SafetyCheckResult(passed=False, reason=reason)

        cost = quantity * price
        reason = await self._limits.check_buy_investment(cost)
        if reason is not None:
            return SafetyCheckResult(passed=False, reason=reason)

        if portfolio_value is not None and portfolio_value > 0:
            reason = await self._limits.check_allocation(symbol, cost, portfolio_value)
            if reason is not None:
                return SafetyCheckResult(passed=False, reason=reason)

        if self._manual.is_flagged(symbol):
            reason = await self._manual.wait_for_approval(symbol)
            if reason is not None:
                return SafetyCheckResult(passed=False, reason=reason)

        return SafetyCheckResult(passed=True)

    async def can_sell(
        self,
        symbol: str,
        quantity: int,
        price: float,
    ) -> SafetyCheckResult:
        if self._kill_switch.is_active():
            return SafetyCheckResult(
                passed=False,
                reason="Kill switch is active — no orders permitted",
            )

        reason = self._dry_run.check()
        if reason is not None:
            return SafetyCheckResult(passed=True, reason=reason, is_dry_run=True)

        reason = await self._limits.check_trade_count()
        if reason is not None:
            return SafetyCheckResult(passed=False, reason=reason)

        if self._manual.is_flagged(symbol):
            reason = await self._manual.wait_for_approval(symbol)
            if reason is not None:
                return SafetyCheckResult(passed=False, reason=reason)

        return SafetyCheckResult(passed=True)
