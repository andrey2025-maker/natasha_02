from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from random import randint

from app.core.config import SecuritySettings
from app.domain.enums import Platform, SyncState
from app.domain.models import SyncRequest
from app.storage.interfaces import SyncRepository


class SyncBlockedError(PermissionError):
    pass


class SyncCooldownError(PermissionError):
    pass


class SyncExpiredError(ValueError):
    pass


class SyncCodeMismatchError(ValueError):
    pass


@dataclass(slots=True)
class SyncService:
    repository: SyncRepository
    settings: SecuritySettings

    async def create_sync_request(self, profile_code: str, from_platform: Platform, to_platform: Platform) -> SyncRequest:
        await self._assert_not_locked(profile_code)
        active = await self.repository.get_active_by_profile_code(profile_code)
        if active and active.created_at > datetime.utcnow() - timedelta(
            seconds=self.settings.sync_request_cooldown_seconds
        ):
            raise SyncCooldownError("Only one sync request is allowed per minute")

        code = f"{randint(100, 999)} {randint(100, 999)}"
        now = datetime.utcnow()
        sync_request = SyncRequest(
            id=0,
            profile_code=profile_code,
            from_platform=from_platform,
            to_platform=to_platform,
            verification_code=code,
            state=SyncState.PENDING,
            expires_at=now + timedelta(seconds=self.settings.sync_code_ttl_seconds),
            created_at=now,
        )
        return await self.repository.save_request(sync_request)

    async def verify_sync_code(self, sync_request: SyncRequest, user_input: str) -> SyncState:
        if datetime.utcnow() > sync_request.expires_at:
            raise SyncExpiredError("Sync request expired")

        cleaned_input = user_input.replace(" ", "")
        cleaned_original = sync_request.verification_code.replace(" ", "")
        if cleaned_input != cleaned_original:
            await self.repository.mark_failed(sync_request.id)
            raise SyncCodeMismatchError("Invalid verification code")
        await self.repository.mark_confirmed(sync_request.id)
        return SyncState.CONFIRMED

    async def get_active_request(self, profile_code: str) -> SyncRequest | None:
        return await self.repository.get_active_by_profile_code(profile_code)

    async def _assert_not_locked(self, profile_code: str) -> None:
        since = datetime.utcnow() - timedelta(seconds=self.settings.sync_failed_attempt_window_seconds)
        failed_attempts = await self.repository.count_failed_attempts_since(profile_code, since)
        if failed_attempts >= self.settings.sync_failed_attempt_limit:
            raise SyncBlockedError(
                f"Too many failed attempts. Retry after {self.settings.sync_failed_lock_seconds} seconds"
            )
