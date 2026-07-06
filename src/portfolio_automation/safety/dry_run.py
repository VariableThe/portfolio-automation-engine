from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DryRunConfig:
    enabled: bool = True


class DryRunGuard:
    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    @property
    def is_dry_run(self) -> bool:
        return self._enabled

    def check(self) -> str | None:
        if self._enabled:
            return "Dry-run mode is active — order not placed"
        return None
