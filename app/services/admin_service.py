from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.domain.models import UserProfile
from app.storage.interfaces import AdminRepository, UserProfileRepository

_ADMIN_CACHE_TTL_SECONDS = 60.0


@dataclass(slots=True)
class AdminService:
    main_admin_id: int
    admin_repo: AdminRepository
    profile_repo: UserProfileRepository
    _is_admin_cache: dict[int, tuple[float, bool]] = field(default_factory=dict, repr=False)

    async def is_admin(self, telegram_user_id: int) -> bool:
        if telegram_user_id == self.main_admin_id:
            return True
        now = time.monotonic()
        cached = self._is_admin_cache.get(telegram_user_id)
        if cached is not None and now - cached[0] < _ADMIN_CACHE_TTL_SECONDS:
            return cached[1]
        result = await self.admin_repo.is_admin(telegram_user_id)
        self._is_admin_cache[telegram_user_id] = (now, result)
        return result

    def _drop_admin_cache(self, telegram_user_id: int) -> None:
        self._is_admin_cache.pop(telegram_user_id, None)

    async def add_admin(self, actor_id: int, new_admin_id: int) -> bool:
        if actor_id != self.main_admin_id:
            return False
        if new_admin_id == self.main_admin_id:
            return True
        await self.admin_repo.add_admin(new_admin_id, added_by=actor_id)
        self._drop_admin_cache(new_admin_id)
        return True

    async def remove_admin(self, actor_id: int, admin_id: int) -> bool:
        if actor_id != self.main_admin_id:
            return False
        if admin_id == self.main_admin_id:
            return False
        await self.admin_repo.remove_admin(admin_id)
        self._drop_admin_cache(admin_id)
        return True

    async def list_admins(self) -> list[int]:
        items = await self.admin_repo.list_admins()
        unique = {self.main_admin_id, *items}
        return sorted(unique)

    async def list_profiles(self, page: int = 1, page_size: int = 9) -> list[UserProfile]:
        safe_page = max(1, page)
        offset = (safe_page - 1) * page_size
        return await self.profile_repo.list_profiles(limit=page_size, offset=offset)

    async def get_profile(self, code: str) -> UserProfile | None:
        return await self.profile_repo.get_by_code(code.strip())

    async def set_block_status(self, code: str, blocked: bool) -> bool:
        profile = await self.profile_repo.get_by_code(code.strip())
        if not profile:
            return False
        profile.is_blocked_by_admin = blocked
        await self.profile_repo.save(profile)
        return True

    async def search_profiles(self, by: str, query: str, limit: int = 50) -> list[UserProfile]:
        safe_query = query.strip()
        if not safe_query:
            return []
        profiles = await self._all_profiles()
        by_normalized = by.strip().lower()
        result: list[UserProfile] = []
        for profile in profiles:
            if by_normalized == "code":
                if safe_query.lower() in profile.code.lower():
                    result.append(profile)
            elif by_normalized == "name":
                if safe_query.lower() in (profile.name or "").lower():
                    result.append(profile)
            elif by_normalized == "id":
                if safe_query == str(profile.telegram_user_id or ""):
                    result.append(profile)
            elif by_normalized == "tag":
                # For now "tag" maps to name substring search.
                if safe_query.lower() in (profile.name or "").lower():
                    result.append(profile)
            if len(result) >= limit:
                break
        return result

    async def _all_profiles(self) -> list[UserProfile]:
        items: list[UserProfile] = []
        page = 1
        page_size = 200
        while True:
            chunk = await self.list_profiles(page=page, page_size=page_size)
            if not chunk:
                break
            items.extend(chunk)
            if len(chunk) < page_size:
                break
            page += 1
        return items
