"""Configuration for the supported local reading workflow."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ...shared.constants import (
    CONFIG_DIR_NAME,
    CONFIG_FILE_NAME,
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
)

CONTAINER_SECRETS_FILE_ENV = "WECHAT_ARTICLE_READER_SECRETS_FILE"
_MAX_SECRETS_FILE_BYTES = 64 * 1024
_EXAMPLE_SECRET_VALUES = frozenset(
    {
        "replace-with-rotated-key",
        "your-deepseek-api-key-here",
    }
)


class ScraperSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    timeout: int = Field(default=30, ge=1)
    max_retries: int = Field(default=3, ge=0)
    user_agent_rotation: bool = True
    max_response_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    max_content_chars: int = Field(default=500_000, ge=1000, le=2_000_000)
    min_request_interval_seconds: float = Field(default=0.5, ge=0, le=60)
    max_retry_delay_seconds: float = Field(default=10, ge=0, le=60)


class DeepSeekSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    api_key: SecretStr = Field(default=SecretStr(""))
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    model: str = DEFAULT_DEEPSEEK_MODEL
    timeout: int = Field(default=60, ge=1)
    max_output_tokens: int = Field(default=1200, ge=128, le=8000)
    reasoning_token_budget: int = Field(default=4800, ge=0, le=16000)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_input_chars: int = Field(default=50_000, ge=1_000, le=500_000)


class ExportSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    default_output_dir: str = "./output"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WECHAT_ARTICLE_READER_",
        env_file=str(Path(__file__).parent.parent.parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    scraper: ScraperSettings = Field(default_factory=ScraperSettings)
    deepseek: DeepSeekSettings = Field(default_factory=DeepSeekSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)

    @model_validator(mode="after")
    def validate_deepseek(self) -> AppSettings:
        if not self.deepseek.api_key.get_secret_value() and not os.getenv(
            CONTAINER_SECRETS_FILE_ENV
        ):
            logger.warning("默认摘要方法需要 DeepSeek API Key")
        return self


def _parse_dotenv_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _apply_container_secrets_file(settings: AppSettings, path: Path) -> None:
    if not path.is_file() or path.stat().st_size > _MAX_SECRETS_FILE_BYTES:
        raise ValueError("configured container secrets file is missing or too large")
    values = _parse_dotenv_file(path)
    if set(values) - {"WECHAT_ARTICLE_READER_DEEPSEEK__API_KEY"}:
        raise ValueError("container secrets file contains unsupported keys")
    value = values.get("WECHAT_ARTICLE_READER_DEEPSEEK__API_KEY", "").strip()
    if value:
        if value.casefold() in _EXAMPLE_SECRET_VALUES:
            raise ValueError("container secrets file contains an example placeholder")
        if settings.deepseek.api_key.get_secret_value():
            raise ValueError("DeepSeek API Key is configured more than once")
        settings.deepseek.api_key = SecretStr(value)


@lru_cache
def get_settings() -> AppSettings:
    settings = AppSettings()
    secrets_file = os.getenv(CONTAINER_SECRETS_FILE_ENV)
    if secrets_file:
        _apply_container_secrets_file(settings, Path(secrets_file))
    dotenv = _parse_dotenv_file(Path(__file__).parent.parent.parent.parent.parent / ".env")
    if not settings.deepseek.api_key.get_secret_value():
        key = os.getenv("DEEPSEEK_API_KEY") or dotenv.get("DEEPSEEK_API_KEY")
        if key:
            settings.deepseek.api_key = SecretStr(key)
    if settings.export.default_output_dir == "./output":
        settings.export.default_output_dir = os.getenv("OUTPUT_DIR") or dotenv.get(
            "OUTPUT_DIR", "./output"
        )
    return settings


def reset_settings() -> None:
    get_settings.cache_clear()


def get_config_path() -> Path:
    return Path.home() / CONFIG_DIR_NAME / CONFIG_FILE_NAME
