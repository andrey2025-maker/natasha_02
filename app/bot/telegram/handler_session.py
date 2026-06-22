from __future__ import annotations

import time
from typing import Any

from aiogram.types import CallbackQuery, Message, TelegramObject, User

from app.core.container import AppContainer
from app.domain.enums import Platform
from app.domain.models import UserProfile, UserSession

USER_SESSION_DATA_KEY = "user_session"
_SESSION_CACHE_TTL_SECONDS = 45.0
_session_cache: dict[int, tuple[float, UserSession | None]] = {}


def drop_user_session_cache(telegram_user_id: int) -> None:
    _session_cache.pop(int(telegram_user_id), None)


def get_preloaded_session(data: dict[str, Any]) -> UserSession | None:
    if USER_SESSION_DATA_KEY not in data:
        return None
    session = data[USER_SESSION_DATA_KEY]
    return session if isinstance(session, UserSession) else None


def store_session(data: dict[str, Any], session: UserSession | None) -> None:
    data[USER_SESSION_DATA_KEY] = session


async def fetch_user_session(
    container: AppContainer,
    platform: Platform,
    platform_user_id: int,
) -> UserSession | None:
    if platform != Platform.TELEGRAM:
        return await container.session_repo.get(platform, platform_user_id)
    now = time.monotonic()
    cached = _session_cache.get(int(platform_user_id))
    if cached is not None and now - cached[0] < _SESSION_CACHE_TTL_SECONDS:
        return cached[1]
    session = await container.session_repo.get(platform, platform_user_id)
    _session_cache[int(platform_user_id)] = (now, session)
    return session


def _user_id_from_context(event: TelegramObject | None, data: dict[str, Any]) -> int | None:
    if isinstance(event, Message) and event.from_user:
        return int(event.from_user.id)
    if isinstance(event, CallbackQuery) and event.from_user:
        return int(event.from_user.id)
    event_user = data.get("event_from_user")
    if isinstance(event_user, User):
        return int(event_user.id)
    return None


async def preload_user_session(
    data: dict[str, Any],
    container: AppContainer,
    platform: Platform,
    platform_user_id: int,
) -> UserSession | None:
    session = get_preloaded_session(data)
    if session is not None:
        return session
    session = await fetch_user_session(container, platform, platform_user_id)
    store_session(data, session)
    return session


async def resolve_user_session(
    data: dict[str, Any],
    container: AppContainer,
    platform: Platform,
    platform_user_id: int,
    *,
    create: bool = True,
    known_profile: UserProfile | None = None,
) -> UserSession | None:
    session = get_preloaded_session(data)
    if session is not None:
        return session
    if not create:
        session = await fetch_user_session(container, platform, platform_user_id)
        store_session(data, session)
        return session
    session = await container.profile_flow.get_or_create_session(
        platform,
        platform_user_id,
        known_profile=known_profile,
    )
    store_session(data, session)
    if platform == Platform.TELEGRAM:
        _session_cache[int(platform_user_id)] = (time.monotonic(), session)
    return session


def handler_data(user_session: UserSession | None = None) -> dict[str, Any]:
    return {USER_SESSION_DATA_KEY: user_session}
