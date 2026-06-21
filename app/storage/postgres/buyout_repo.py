from __future__ import annotations

import asyncpg

from app.domain.enums import DeliveryFlowType, OrderStatus
from app.domain.enums import Platform
from app.domain.models import BuyoutOrder, OrderMediaItem, OrderStatusHistoryItem
from app.storage.interfaces import BuyoutOrderRepository


def _row_to_order(row: asyncpg.Record) -> BuyoutOrder:
    return BuyoutOrder(
        id=row["id"],
        user_profile_id=row["user_profile_id"],
        order_number=row["order_number"],
        flow_type=DeliveryFlowType(row["flow_type"]),
        status=OrderStatus(row["status"]),
        product_url=row["product_url"],
        quantity_text=row["quantity_text"],
        media_group_id=row["media_group_id"],
        media_storage_chat_id=row["media_storage_chat_id"],
        media_storage_topic_id=row["media_storage_topic_id"],
        media_storage_message_id=row["media_storage_message_id"],
        media_vk_attachment=row["media_vk_attachment"],
        price_rub=row["price_rub"],
        track_number=row["track_number"],
        manager_comment=row["manager_comment"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_history(row: asyncpg.Record) -> OrderStatusHistoryItem:
    return OrderStatusHistoryItem(
        id=row["id"],
        order_id=row["order_id"],
        previous_status=OrderStatus(row["previous_status"]) if row["previous_status"] else None,
        new_status=OrderStatus(row["new_status"]),
        changed_by_platform=Platform(row["changed_by_platform"]),
        changed_by_user_id=row["changed_by_user_id"],
        note=row["note"],
        changed_at=row["changed_at"],
    )


def _row_to_media(row: asyncpg.Record) -> OrderMediaItem:
    return OrderMediaItem(
        id=row["id"],
        order_id=row["order_id"],
        platform=Platform(row["platform"]),
        media_type=row["media_type"],
        tg_chat_id=row["tg_chat_id"],
        tg_topic_id=row["tg_topic_id"],
        tg_message_id=row["tg_message_id"],
        tg_file_id=row["tg_file_id"],
        vk_attachment=row["vk_attachment"],
        created_at=row["created_at"],
    )


class PostgresBuyoutOrderRepository(BuyoutOrderRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, order: BuyoutOrder) -> BuyoutOrder:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO buyout_orders (
                    user_profile_id, order_number, flow_type, status,
                    product_url, quantity_text, media_group_id, media_storage_chat_id,
                    media_storage_topic_id, media_storage_message_id, media_vk_attachment,
                    price_rub, track_number, manager_comment,
                    created_at, updated_at
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                RETURNING *
                """,
                order.user_profile_id,
                order.order_number,
                order.flow_type.value,
                order.status.value,
                order.product_url,
                order.quantity_text,
                order.media_group_id,
                order.media_storage_chat_id,
                order.media_storage_topic_id,
                order.media_storage_message_id,
                order.media_vk_attachment,
                order.price_rub,
                order.track_number,
                order.manager_comment,
                order.created_at,
                order.updated_at,
            )
        return _row_to_order(row)

    async def count_for_user(self, user_profile_id: int) -> int:
        async with self._pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM buyout_orders WHERE user_profile_id = $1", user_profile_id)
        return int(count or 0)

    async def count_all(self, statuses: list[str] | None = None) -> int:
        async with self._pool.acquire() as conn:
            if statuses:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM buyout_orders WHERE status = ANY($1::text[])",
                    statuses,
                )
            else:
                count = await conn.fetchval("SELECT COUNT(*) FROM buyout_orders")
        return int(count or 0)

    async def list_all_recent(
        self,
        limit: int = 9,
        offset: int = 0,
        statuses: list[str] | None = None,
    ) -> list[BuyoutOrder]:
        async with self._pool.acquire() as conn:
            if statuses:
                rows = await conn.fetch(
                    """
                    SELECT * FROM buyout_orders
                    WHERE status = ANY($1::text[])
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    statuses,
                    limit,
                    offset,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM buyout_orders
                    ORDER BY created_at DESC
                    LIMIT $1 OFFSET $2
                    """,
                    limit,
                    offset,
                )
        return [_row_to_order(row) for row in rows]

    async def list_for_user(
        self,
        user_profile_id: int,
        limit: int = 9,
        offset: int = 0,
        statuses: list[str] | None = None,
    ) -> list[BuyoutOrder]:
        if statuses:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM buyout_orders
                    WHERE user_profile_id = $1 AND status = ANY($2::text[])
                    ORDER BY created_at DESC
                    LIMIT $3 OFFSET $4
                    """,
                    user_profile_id,
                    statuses,
                    limit,
                    offset,
                )
            return [_row_to_order(row) for row in rows]

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM buyout_orders
                WHERE user_profile_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                user_profile_id,
                limit,
                offset,
            )
        return [_row_to_order(row) for row in rows]

    async def get_by_order_number(self, order_number: str) -> BuyoutOrder | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM buyout_orders WHERE order_number = $1",
                order_number,
            )
        return _row_to_order(row) if row else None

    async def update(self, order: BuyoutOrder) -> BuyoutOrder:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE buyout_orders
                SET status = $2, product_url = $3, quantity_text = $4, media_group_id = $5,
                    media_storage_chat_id = $6, media_storage_topic_id = $7, media_storage_message_id = $8,
                    media_vk_attachment = $9, price_rub = $10, track_number = $11,
                    manager_comment = $12, updated_at = $13
                WHERE id = $1
                RETURNING *
                """,
                order.id,
                order.status.value,
                order.product_url,
                order.quantity_text,
                order.media_group_id,
                order.media_storage_chat_id,
                order.media_storage_topic_id,
                order.media_storage_message_id,
                order.media_vk_attachment,
                order.price_rub,
                order.track_number,
                order.manager_comment,
                order.updated_at,
            )
        if not row:
            raise ValueError("Order not found")
        return _row_to_order(row)

    async def add_status_history(self, item: OrderStatusHistoryItem) -> OrderStatusHistoryItem:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO buyout_order_status_history (
                    order_id, previous_status, new_status, changed_by_platform,
                    changed_by_user_id, note, changed_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING *
                """,
                item.order_id,
                item.previous_status.value if item.previous_status else None,
                item.new_status.value,
                item.changed_by_platform.value,
                item.changed_by_user_id,
                item.note,
                item.changed_at,
            )
        return _row_to_history(row)

    async def list_status_history(self, order_id: int, limit: int = 20) -> list[OrderStatusHistoryItem]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM buyout_order_status_history
                WHERE order_id = $1
                ORDER BY changed_at DESC
                LIMIT $2
                """,
                order_id,
                limit,
            )
        return [_row_to_history(row) for row in rows]

    async def count_by_status(self) -> dict[str, int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT status, COUNT(*)::int AS total
                FROM buyout_orders
                GROUP BY status
                """
            )
        return {str(row["status"]): int(row["total"]) for row in rows}

    async def add_order_media(self, item: OrderMediaItem) -> OrderMediaItem:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO order_media (
                    order_id, platform, media_type,
                    tg_chat_id, tg_topic_id, tg_message_id, tg_file_id, vk_attachment, created_at
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                RETURNING *
                """,
                item.order_id,
                item.platform.value,
                item.media_type,
                item.tg_chat_id,
                item.tg_topic_id,
                item.tg_message_id,
                item.tg_file_id,
                item.vk_attachment,
                item.created_at,
            )
        return _row_to_media(row)

    async def list_order_media(self, order_id: int) -> list[OrderMediaItem]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM order_media
                WHERE order_id = $1
                ORDER BY created_at ASC, id ASC
                """,
                order_id,
            )
        return [_row_to_media(row) for row in rows]

    async def search_orders(self, by: str, query: str, limit: int = 30) -> list[BuyoutOrder]:
        mode = by.strip().lower()
        needle = query.strip()
        if not needle:
            return []
        safe_limit = max(1, min(limit, 100))
        pattern = f"%{needle}%"
        async with self._pool.acquire() as conn:
            if mode == "order_number":
                rows = await conn.fetch(
                    """
                    SELECT * FROM buyout_orders
                    WHERE order_number ILIKE $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    pattern,
                    safe_limit,
                )
            elif mode == "code":
                rows = await conn.fetch(
                    """
                    SELECT o.* FROM buyout_orders o
                    JOIN user_profiles p ON p.id = o.user_profile_id
                    WHERE p.code ILIKE $1
                    ORDER BY o.created_at DESC
                    LIMIT $2
                    """,
                    pattern,
                    safe_limit,
                )
            elif mode == "track":
                rows = await conn.fetch(
                    """
                    SELECT * FROM buyout_orders
                    WHERE track_number ILIKE $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    pattern,
                    safe_limit,
                )
            else:
                return []
        return [_row_to_order(row) for row in rows]

    async def list_orders_with_exact_tracks(self, tracks: list[str]) -> list[BuyoutOrder]:
        from app.services.track_match_utils import normalize_track

        normalized = sorted({normalize_track(item) for item in tracks if normalize_track(item)})
        if not normalized:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM buyout_orders
                WHERE track_number IS NOT NULL
                  AND LOWER(TRIM(track_number)) = ANY($1::text[])
                ORDER BY created_at DESC
                """,
                normalized,
            )
        return [_row_to_order(row) for row in rows]
