from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from portfolio_automation.core.config import ConfigModel, load_config


@pytest.fixture
def sample_config() -> ConfigModel:
    return load_config(Path("config"))


@pytest.fixture
def sample_rules() -> dict:
    rules_path = Path("config/rules.yaml")
    if rules_path.exists():
        with open(rules_path) as f:
            return yaml.safe_load(f) or {}
    return {"sell_rules": [], "buy_queue": []}


@pytest.fixture
def mock_config() -> ConfigModel:
    return ConfigModel()
