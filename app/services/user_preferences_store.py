from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import Platform
from app.services.admin_tools_service import DbSettingsStore


@dataclass(slots=True)
class UserPreferencesStore:
    database_dsn: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def get_order_filters(self, platform: Platform, platform_user_id: int) -> list[str] | None:
        payload = await self._db.get(self._filters_key(platform, platform_user_id))
        if not isinstance(payload, dict):
            return None
        raw = payload.get("order_filters")
        if not isinstance(raw, list):
            return None
        values = [str(item).strip() for item in raw if str(item).strip()]
        return values or None

    async def save_order_filters(
        self,
        platform: Platform,
        platform_user_id: int,
        filters: list[str],
    ) -> None:
        await self._db.set(
            self._filters_key(platform, platform_user_id),
            {"order_filters": [str(item) for item in filters]},
        )

    @staticmethod
    def _filters_key(platform: Platform, platform_user_id: int) -> str:
        return f"user_prefs:{platform.value}:{int(platform_user_id)}"
