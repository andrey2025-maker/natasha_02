from __future__ import annotations

import json
import logging

import asyncpg

logger = logging.getLogger(__name__)

_MIGRATION_KEY = "migration:topic_dialog_links_v1"
_LINKS_SETTINGS_KEY = "topic_dialog_links"


async def migrate_topic_dialog_links_from_settings(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        if await _is_migration_done(conn):
            return

        payload = await _load_legacy_links_payload(conn)
        if payload:
            inserted = await _import_legacy_links(conn, payload)
            logger.info("Migrated %s topic dialog message links from app_settings JSON", inserted)
        else:
            logger.info("No legacy topic_dialog_links JSON payload to migrate")

        await conn.execute(
            """
            INSERT INTO app_settings(key, value, updated_at)
            VALUES($1, 'true'::jsonb, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = NOW()
            """,
            _MIGRATION_KEY,
        )


async def _is_migration_done(conn: asyncpg.Connection) -> bool:
    row = await conn.fetchrow("SELECT value FROM app_settings WHERE key = $1", _MIGRATION_KEY)
    if not row:
        return False
    value = row["value"]
    return value is True or value == "true"


async def _load_legacy_links_payload(conn: asyncpg.Connection) -> dict | None:
    row = await conn.fetchrow("SELECT value FROM app_settings WHERE key = $1", _LINKS_SETTINGS_KEY)
    if not row:
        return None
    value = row["value"]
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


async def _import_legacy_links(conn: asyncpg.Connection, payload: dict) -> int:
    records: list[tuple[int, int, int, str, int]] = []
    for topic_key, topic_payload in payload.items():
        if not isinstance(topic_key, str) or not isinstance(topic_payload, dict):
            continue
        try:
            chat_part, topic_part = topic_key.split(":", maxsplit=1)
            chat_id = int(chat_part)
            topic_id = int(topic_part)
        except (TypeError, ValueError):
            continue

        for message_id_raw, link_payload in topic_payload.items():
            if not isinstance(link_payload, dict):
                continue
            try:
                topic_message_id = int(message_id_raw)
            except (TypeError, ValueError):
                continue
            platform = str(link_payload.get("platform", "")).strip().lower()
            try:
                platform_user_id = int(link_payload.get("platform_user_id"))
            except (TypeError, ValueError):
                continue
            if not platform or platform_user_id <= 0:
                continue
            records.append((chat_id, topic_id, topic_message_id, platform, platform_user_id))

    if not records:
        return 0

    await conn.executemany(
        """
        INSERT INTO topic_dialog_message_links (
            chat_id, topic_id, topic_message_id, platform, platform_user_id
        )
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (chat_id, topic_id, topic_message_id) DO UPDATE
        SET platform = EXCLUDED.platform,
            platform_user_id = EXCLUDED.platform_user_id
        """,
        records,
    )
    return len(records)
