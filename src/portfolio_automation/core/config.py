from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BrokerConfig(BaseModel):
    type: str = "mock"
    api_key: str = ""
    api_secret: str = ""
    user_id: str = ""
    access_token: str = ""


class ExecutionConfig(BaseModel):
    poll_interval_seconds: int = 60
    market_open: str = "09:15"
    market_close: str = "15:30"
    buy_queue_on_failure: str = "skip"


class SafetyConfig(BaseModel):
    dry_run: bool = True
    max_daily_trades: int = 10
    max_daily_investment: float = 100000.0
    max_allocation_per_stock: float = 0.20
    cooldown_minutes: int = 30
    manual_confirmation: bool = False
    kill_switch_enabled: bool = True


class DatabaseConfig(BaseModel):
    path: str = "data/portfolio.db"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "console"
    file_path: str | None = None


class ConsoleChannelConfig(BaseModel):
    enabled: bool = True
    level: str = "INFO"


class DesktopChannelConfig(BaseModel):
    enabled: bool = False


class DiscordChannelConfig(BaseModel):
    enabled: bool = False
    webhook_url: str = ""


class TelegramChannelConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


class NotificationChannelsConfig(BaseModel):
    console: ConsoleChannelConfig = Field(default_factory=ConsoleChannelConfig)
    desktop: DesktopChannelConfig = Field(default_factory=DesktopChannelConfig)
    discord: DiscordChannelConfig = Field(default_factory=DiscordChannelConfig)
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)


class NotificationConfig(BaseModel):
    enabled: bool = True
    channels: NotificationChannelsConfig = Field(
        default_factory=NotificationChannelsConfig
    )
    discord_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    def model_post_init(self, __context: Any) -> None:
        if self.discord_webhook_url and not self.channels.discord.webhook_url:
            self.channels.discord.webhook_url = self.discord_webhook_url
        elif self.channels.discord.webhook_url:
            self.discord_webhook_url = self.channels.discord.webhook_url

        if self.telegram_bot_token and not self.channels.telegram.bot_token:
            self.channels.telegram.bot_token = self.telegram_bot_token
        elif self.channels.telegram.bot_token:
            self.telegram_bot_token = self.channels.telegram.bot_token

        if self.telegram_chat_id and not self.channels.telegram.chat_id:
            self.channels.telegram.chat_id = self.telegram_chat_id
        elif self.channels.telegram.chat_id:
            self.telegram_chat_id = self.channels.telegram.chat_id


class ConfigModel(BaseModel):
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)


class EnvOverrides(BaseSettings):
    broker_type: str | None = None
    broker_api_key: str | None = None
    broker_api_secret: str | None = None
    broker_user_id: str | None = None
    broker_access_token: str | None = None
    discord_webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    database_path: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def _expand_env_vars(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(v) for v in obj]
    if isinstance(obj, str):
        val = os.path.expandvars(obj)
        if val.startswith("${") and val.endswith("}"):
            return ""
        return val
    return obj


def load_config(config_dir: str | Path = "config") -> ConfigModel:
    config_dir = Path(config_dir)
    config_path = config_dir / "default.yaml"

    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    raw = _expand_env_vars(raw)
    config = ConfigModel(**raw)

    overrides = EnvOverrides()

    if overrides.broker_type is not None:
        config.broker.type = overrides.broker_type
    if overrides.broker_api_key is not None:
        config.broker.api_key = overrides.broker_api_key
    if overrides.broker_api_secret is not None:
        config.broker.api_secret = overrides.broker_api_secret
    if overrides.broker_user_id is not None:
        config.broker.user_id = overrides.broker_user_id
    if overrides.broker_access_token is not None:
        config.broker.access_token = overrides.broker_access_token
    if overrides.discord_webhook_url is not None:
        config.notifications.discord_webhook_url = overrides.discord_webhook_url
        config.notifications.channels.discord.webhook_url = (
            overrides.discord_webhook_url
        )
    if overrides.telegram_bot_token is not None:
        config.notifications.telegram_bot_token = overrides.telegram_bot_token
        config.notifications.channels.telegram.bot_token = overrides.telegram_bot_token
    if overrides.telegram_chat_id is not None:
        config.notifications.telegram_chat_id = overrides.telegram_chat_id
        config.notifications.channels.telegram.chat_id = overrides.telegram_chat_id
    if overrides.database_path is not None:
        config.database.path = overrides.database_path

    return config
