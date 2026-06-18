from __future__ import annotations

from dataclasses import dataclass
from os import getenv


@dataclass(frozen=True)
class DatabaseSettings:
    dsn: str


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    main_admin_id: int
    backup_chat_id: int | None = None
    backup_topic_id: int | None = None
    error_chat_id: int | None = None
    error_topic_id: int | None = None


@dataclass(frozen=True)
class VkSettings:
    bot_token: str
    group_id: int
    error_peer_id: int | None = None


@dataclass(frozen=True)
class SecuritySettings:
    callback_secret: str
    sync_code_ttl_seconds: int = 120
    sync_request_cooldown_seconds: int = 60
    sync_failed_attempt_window_seconds: int = 300
    sync_failed_attempt_limit: int = 3
    sync_failed_lock_seconds: int = 900


@dataclass(frozen=True)
class RateLimitSettings:
    per_second_limit: int = 3
    per_minute_limit: int = 30
    duplicate_message_limit: int = 3
    user_max_text_len: int = 5000
    user_max_media_size_mb: int = 20


@dataclass(frozen=True)
class AppSettings:
    database: DatabaseSettings
    telegram: TelegramSettings
    vk: VkSettings | None
    security: SecuritySettings
    rate_limits: RateLimitSettings


def _require(name: str) -> str:
    value = getenv(name, "")
    if not value:
        raise ValueError(f"{name} is required")
    return value


def load_settings() -> AppSettings:
    return AppSettings(
        database=DatabaseSettings(dsn=_require("DATABASE_URL")),
        telegram=TelegramSettings(
            bot_token=_require("TELEGRAM_BOT_TOKEN"),
            main_admin_id=int(_require("MAIN_ADMIN_ID")),
            backup_chat_id=_optional_int("BACKUP_CHAT_ID"),
            backup_topic_id=_optional_int("BACKUP_TOPIC_ID"),
            error_chat_id=_optional_int("TG_ERROR_CHAT_ID"),
            error_topic_id=_optional_int("TG_ERROR_TOPIC_ID"),
        ),
        vk=_load_vk_settings(),
        security=SecuritySettings(callback_secret=_require("CALLBACK_SECRET")),
        rate_limits=RateLimitSettings(),
    )


def _load_vk_settings() -> VkSettings | None:
    token = getenv("VK_BOT_TOKEN", "").strip()
    group_id = getenv("VK_GROUP_ID", "").strip()
    if not token or not group_id:
        return None
    return VkSettings(
        bot_token=token,
        group_id=int(group_id),
        error_peer_id=_optional_int("VK_ERROR_PEER_ID"),
    )


def _optional_int(name: str) -> int | None:
    raw = getenv(name, "").strip()
    if not raw:
        return None
    return int(raw)
