from __future__ import annotations

from typing import Any

from app.core.container import AppContainer
from app.domain.enums import Platform
from app.domain.models import UserProfile, UserSession

USER_SESSION_DATA_KEY = "user_session"


def get_preloaded_session(data: dict[str, Any]) -> UserSession | None:
    if USER_SESSION_DATA_KEY not in data:
        return None
    session = data[USER_SESSION_DATA_KEY]
    return session if isinstance(session, UserSession) else None


def store_session(data: dict[str, Any], session: UserSession | None) -> None:
    data[USER_SESSION_DATA_KEY] = session


async def resolve_user_session(
    data: dict[str, Any],
    container: AppContainer,
    platform: Platform,
    platform_user_id: int,
    *,
    create: bool = True,
    known_profile: UserProfile | None = None,
) -> UserSession | None:
    """Один запрос сессии на update: middleware preload + create при необходимости."""
    session = get_preloaded_session(data)
    if session is not None:
        return session
    if not create:
        session = await container.session_repo.get(platform, platform_user_id)
        store_session(data, session)
        return session
    session = await container.profile_flow.get_or_create_session(
        platform,
        platform_user_id,
        known_profile=known_profile,
    )
    store_session(data, session)
    return session


def handler_data(user_session: UserSession | None = None) -> dict[str, Any]:
    return {USER_SESSION_DATA_KEY: user_session}
