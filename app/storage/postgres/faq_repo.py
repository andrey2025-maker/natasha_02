from __future__ import annotations

import asyncpg

from app.domain.models import FaqSection
from app.storage.interfaces import FaqRepository


def _row_to_section(row: asyncpg.Record) -> FaqSection:
    return FaqSection(
        id=row["id"],
        parent_id=row["parent_id"],
        title=row["title"],
        content_text=row["content_text"],
        sort_order=row["sort_order"],
        is_active=row["is_active"],
        created_at=row["created_at"],
    )


class PostgresFaqRepository(FaqRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_children(self, parent_id: int | None) -> list[FaqSection]:
        async with self._pool.acquire() as conn:
            if parent_id is None:
                rows = await conn.fetch(
                    """
                    SELECT * FROM faq_sections
                    WHERE parent_id IS NULL AND is_active = TRUE
                    ORDER BY sort_order, id
                    """
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM faq_sections
                    WHERE parent_id = $1 AND is_active = TRUE
                    ORDER BY sort_order, id
                    """,
                    parent_id,
                )
        return [_row_to_section(row) for row in rows]

    async def get_by_id(self, section_id: int) -> FaqSection | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM faq_sections WHERE id = $1", section_id)
        return _row_to_section(row) if row else None

    async def create(self, section: FaqSection) -> FaqSection:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO faq_sections (parent_id, title, content_text, sort_order, is_active, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING *
                """,
                section.parent_id,
                section.title,
                section.content_text,
                section.sort_order,
                section.is_active,
                section.created_at,
            )
        return _row_to_section(row)

    async def update(self, section: FaqSection) -> FaqSection:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE faq_sections
                SET parent_id = $2, title = $3, content_text = $4, sort_order = $5, is_active = $6
                WHERE id = $1
                RETURNING *
                """,
                section.id,
                section.parent_id,
                section.title,
                section.content_text,
                section.sort_order,
                section.is_active,
            )
        if not row:
            raise ValueError("FAQ section not found")
        return _row_to_section(row)

    async def delete(self, section_id: int) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM faq_sections WHERE id = $1", section_id)
        return result.endswith("1")
