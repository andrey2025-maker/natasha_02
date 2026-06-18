from __future__ import annotations

import asyncpg

from app.domain.enums import OutboundMessageStatus, Platform
from app.domain.models import OutboundMessage
from app.storage.interfaces import OutboundMessageRepository
from app.storage.postgres.pool import dumps_state_data, loads_state_data


def _row_to_outbound(row: asyncpg.Record) -> OutboundMessage:
    return OutboundMessage(
        id=row["id"],
        platform=Platform(row["platform"]),
        platform_user_id=row["platform_user_id"],
        message_type=row["message_type"],
        payload=loads_state_data(row["payload"]),
        status=OutboundMessageStatus(row["status"]),
        created_at=row["created_at"],
    )


class PostgresOutboundMessageRepository(OutboundMessageRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def enqueue(self, message: OutboundMessage) -> OutboundMessage:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO outbound_messages (
                    platform, platform_user_id, message_type, payload, status, created_at
                )
                VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                RETURNING *
                """,
                message.platform.value,
                message.platform_user_id,
                message.message_type,
                dumps_state_data(message.payload),
                message.status.value,
                message.created_at,
            )
        return _row_to_outbound(row)

    async def claim_pending(self, platform: Platform, limit: int = 50) -> list[OutboundMessage]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM outbound_messages
                WHERE id IN (
                    SELECT id
                    FROM outbound_messages
                    WHERE platform = $1 AND status = 'pending'
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT $2
                )
                ORDER BY created_at
                """,
                platform.value,
                limit,
            )
        return [_row_to_outbound(row) for row in rows]

    async def mark_sent(self, message_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE outbound_messages SET status = 'sent' WHERE id = $1",
                message_id,
            )

    async def mark_failed(self, message_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE outbound_messages SET status = 'failed' WHERE id = $1",
                message_id,
            )
