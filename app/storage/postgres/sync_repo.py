from __future__ import annotations

from datetime import datetime

import asyncpg

from app.domain.enums import Platform, SyncState
from app.domain.models import SyncRequest
from app.storage.interfaces import SyncRepository


def _row_to_sync(row: asyncpg.Record) -> SyncRequest:
    return SyncRequest(
        id=row["id"],
        profile_code=row["profile_code"],
        from_platform=Platform(row["from_platform"]),
        to_platform=Platform(row["to_platform"]),
        verification_code=row["verification_code"],
        state=SyncState(row["state"]),
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


class PostgresSyncRepository(SyncRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_request(self, sync_request: SyncRequest) -> SyncRequest:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO sync_requests (
                    profile_code, from_platform, to_platform,
                    verification_code, state, expires_at, created_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING *
                """,
                sync_request.profile_code,
                sync_request.from_platform.value,
                sync_request.to_platform.value,
                sync_request.verification_code,
                sync_request.state.value,
                sync_request.expires_at,
                sync_request.created_at,
            )
        return _row_to_sync(row)

    async def get_active_by_profile_code(self, profile_code: str) -> SyncRequest | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM sync_requests
                WHERE profile_code = $1 AND state = 'pending' AND expires_at > NOW()
                ORDER BY created_at DESC
                LIMIT 1
                """,
                profile_code,
            )
        return _row_to_sync(row) if row else None

    async def count_failed_attempts_since(self, profile_code: str, since: datetime) -> int:
        async with self._pool.acquire() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM sync_requests
                WHERE profile_code = $1 AND state = 'failed' AND created_at >= $2
                """,
                profile_code,
                since,
            )
        return int(count or 0)

    async def mark_failed(self, sync_request_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE sync_requests SET state = 'failed' WHERE id = $1",
                sync_request_id,
            )

    async def mark_confirmed(self, sync_request_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE sync_requests SET state = 'confirmed' WHERE id = $1",
                sync_request_id,
            )

    async def rename_active_profile_code(self, old_code: str, new_code: str) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE sync_requests
                SET profile_code = $2
                WHERE profile_code = $1
                  AND state = 'pending'
                  AND expires_at > NOW()
                """,
                old_code,
                new_code,
            )
        try:
            return int(str(result).split()[-1])
        except (TypeError, ValueError):
            return 0
