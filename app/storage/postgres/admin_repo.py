from __future__ import annotations

import asyncpg

from app.storage.interfaces import AdminRepository


class PostgresAdminRepository(AdminRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def is_admin(self, telegram_user_id: int) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM admin_users WHERE telegram_user_id = $1",
                telegram_user_id,
            )
        return row is not None

    async def add_admin(self, telegram_user_id: int, added_by: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO admin_users (telegram_user_id, added_by)
                VALUES ($1, $2)
                ON CONFLICT (telegram_user_id) DO NOTHING
                """,
                telegram_user_id,
                added_by,
            )

    async def remove_admin(self, telegram_user_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM admin_users WHERE telegram_user_id = $1",
                telegram_user_id,
            )

    async def list_admins(self) -> list[int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT telegram_user_id FROM admin_users ORDER BY added_at DESC",
            )
        return [int(row["telegram_user_id"]) for row in rows]
