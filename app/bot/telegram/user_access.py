from __future__ import annotations

import time

from app.core.container import AppContainer
from app.domain.enums import Platform

_BLOCKED_CACHE_TTL_SECONDS = 30.0
_blocked_cache: dict[int, tuple[float, bool]] = {}


def drop_blocked_cache(telegram_user_id: int) -> None:
    _blocked_cache.pop(int(telegram_user_id), None)


async def is_user_blocked_by_admin(container: AppContainer, telegram_user_id: int) -> bool:
    now = time.monotonic()
    cached = _blocked_cache.get(telegram_user_id)
    if cached is not None and now - cached[0] < _BLOCKED_CACHE_TTL_SECONDS:
        return cached[1]
    profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, telegram_user_id)
    blocked = bool(profile and profile.is_blocked_by_admin)
    _blocked_cache[telegram_user_id] = (now, blocked)
    return blocked
