from __future__ import annotations

import asyncpg

from app.storage.interfaces import CodeReserveRepository


class PostgresCodeReserveRepository(CodeReserveRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def add_codes(self, codes: list[str]) -> list[str]:
        added: list[str] = []
        async with self._pool.acquire() as conn:
            for code in codes:
                exists_profile = await conn.fetchval(
                    "SELECT 1 FROM user_profiles WHERE code = $1",
                    code,
                )
                if exists_profile:
                    continue
                result = await conn.execute(
                    """
                    INSERT INTO reserved_codes (code)
                    VALUES ($1)
                    ON CONFLICT (code) DO NOTHING
                    """,
                    code,
                )
                if result.endswith("1"):
                    added.append(code)
        return added

    async def remove_codes(self, codes: list[str]) -> list[str]:
        removed: list[str] = []
        async with self._pool.acquire() as conn:
            for code in codes:
                result = await conn.execute("DELETE FROM reserved_codes WHERE code = $1", code)
                if result.endswith("1"):
                    removed.append(code)
        return removed

    async def list_reserved(self) -> list[str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT code FROM reserved_codes ORDER BY code")
        return [row["code"] for row in rows]
