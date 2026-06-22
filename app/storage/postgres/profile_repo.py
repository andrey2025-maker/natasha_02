from __future__ import annotations

from datetime import datetime

import asyncpg

from app.domain.enums import DialogState, Platform
from app.domain.models import UserProfile, UserSession
from app.storage.interfaces import UserProfileRepository
from app.storage.postgres.pool import loads_state_data


def _row_to_profile(row: asyncpg.Record) -> UserProfile:
    return UserProfile(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        phone=row["phone"],
        city=row["city"],
        has_passport=row["has_passport"],
        telegram_user_id=row["telegram_user_id"],
        vk_user_id=row["vk_user_id"],
        is_blocked_by_admin=row["is_blocked_by_admin"],
        blocked_bot=row["blocked_bot"],
        created_at=row["created_at"],
        last_activity_at=row["last_activity_at"],
    )


class PostgresUserProfileRepository(UserProfileRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_by_id(self, profile_id: int) -> UserProfile | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM user_profiles WHERE id = $1", profile_id)
        return _row_to_profile(row) if row else None

    async def get_by_code(self, code: str) -> UserProfile | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM user_profiles WHERE code = $1", code)
        return _row_to_profile(row) if row else None

    async def get_by_platform_user(self, platform: Platform, platform_user_id: int) -> UserProfile | None:
        column = "telegram_user_id" if platform == Platform.TELEGRAM else "vk_user_id"
        query = f"SELECT * FROM user_profiles WHERE {column} = $1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, platform_user_id)
        return _row_to_profile(row) if row else None

    async def save(self, profile: UserProfile) -> UserProfile:
        async with self._pool.acquire() as conn:
            if profile.id:
                row = await conn.fetchrow(
                    """
                    UPDATE user_profiles
                    SET code = $2, name = $3, phone = $4, city = $5, has_passport = $6,
                        telegram_user_id = $7, vk_user_id = $8,
                        is_blocked_by_admin = $9, blocked_bot = $10,
                        last_activity_at = $11
                    WHERE id = $1
                    RETURNING *
                    """,
                    profile.id,
                    profile.code,
                    profile.name,
                    profile.phone,
                    profile.city,
                    profile.has_passport,
                    profile.telegram_user_id,
                    profile.vk_user_id,
                    profile.is_blocked_by_admin,
                    profile.blocked_bot,
                    profile.last_activity_at,
                )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO user_profiles (
                        code, name, phone, city, has_passport,
                        telegram_user_id, vk_user_id,
                        is_blocked_by_admin, blocked_bot,
                        created_at, last_activity_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    RETURNING *
                    """,
                    profile.code,
                    profile.name,
                    profile.phone,
                    profile.city,
                    profile.has_passport,
                    profile.telegram_user_id,
                    profile.vk_user_id,
                    profile.is_blocked_by_admin,
                    profile.blocked_bot,
                    profile.created_at,
                    profile.last_activity_at,
                )
        return _row_to_profile(row)

    async def next_available_code(self) -> str:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT LPAD(n::text, 3, '0') AS code
                FROM generate_series(1, 9999) AS n
                WHERE LPAD(n::text, 3, '0') NOT IN (SELECT code FROM user_profiles)
                  AND LPAD(n::text, 3, '0') NOT IN (SELECT code FROM reserved_codes)
                ORDER BY n
                LIMIT 1
                """
            )
        if not row:
            raise RuntimeError("No available profile codes")
        return row["code"]

    async def is_code_reserved(self, code: str) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT 1 FROM reserved_codes WHERE code = $1", code)
        return row is not None

    async def is_code_taken(self, code: str) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT 1 FROM user_profiles WHERE code = $1", code)
        return row is not None

    async def list_profiles(self, limit: int = 9, offset: int = 0) -> list[UserProfile]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM user_profiles
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        return [_row_to_profile(row) for row in rows]

    async def count_all(self) -> int:
        async with self._pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM user_profiles")
        return int(count or 0)

    async def count_active(self) -> int:
        async with self._pool.acquire() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM user_profiles
                WHERE is_blocked_by_admin = FALSE AND blocked_bot = FALSE
                """
            )
        return int(count or 0)

    async def count_blocked_by_admin(self) -> int:
        async with self._pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM user_profiles WHERE is_blocked_by_admin = TRUE"
            )
        return int(count or 0)

    async def count_blocked_bot(self) -> int:
        async with self._pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM user_profiles WHERE blocked_bot = TRUE"
            )
        return int(count or 0)

    async def weekly_registrations(self, weeks: int = 8) -> list[tuple[str, int]]:
        safe_weeks = max(1, min(52, weeks))
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT to_char(date_trunc('week', created_at), 'YYYY-MM-DD') AS week_start,
                       COUNT(*)::int AS total
                FROM user_profiles
                WHERE created_at >= NOW() - ($1::int * INTERVAL '7 days')
                GROUP BY week_start
                ORDER BY week_start DESC
                """,
                safe_weeks,
            )
        return [(str(row["week_start"]), int(row["total"])) for row in rows]


class PostgresSessionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, platform: Platform, platform_user_id: int) -> UserSession | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM user_sessions WHERE platform = $1 AND platform_user_id = $2",
                platform.value,
                platform_user_id,
            )
        return _row_to_session(row) if row else None

    async def save(self, session: UserSession) -> UserSession:
        from app.storage.postgres.pool import dumps_state_data

        async with self._pool.acquire() as conn:
            if session.id:
                row = await conn.fetchrow(
                    """
                    UPDATE user_sessions
                    SET state = $2, state_data = $3::jsonb, user_profile_id = $4, updated_at = $5
                    WHERE id = $1
                    RETURNING *
                    """,
                    session.id,
                    session.state.value,
                    dumps_state_data(session.state_data),
                    session.user_profile_id,
                    datetime.utcnow(),
                )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO user_sessions (platform, platform_user_id, state, state_data, user_profile_id, updated_at)
                    VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                    RETURNING *
                    """,
                    session.platform.value,
                    session.platform_user_id,
                    session.state.value,
                    dumps_state_data(session.state_data),
                    session.user_profile_id,
                    datetime.utcnow(),
                )
        if session.platform == Platform.TELEGRAM:
            from app.bot.telegram.handler_session import drop_user_session_cache

            drop_user_session_cache(int(session.platform_user_id))
        return _row_to_session(row)


def _row_to_session(row: asyncpg.Record) -> UserSession:
    return UserSession(
        id=row["id"],
        platform=Platform(row["platform"]),
        platform_user_id=row["platform_user_id"],
        state=DialogState(row["state"]),
        state_data=loads_state_data(row["state_data"]),
        user_profile_id=row["user_profile_id"],
        updated_at=row["updated_at"],
    )
