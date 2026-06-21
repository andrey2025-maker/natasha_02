from __future__ import annotations

import json
from pathlib import Path

import asyncpg

from app.core.config import DatabaseSettings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


async def create_pool(settings: DatabaseSettings) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=settings.dsn, min_size=1, max_size=10)


async def init_schema(pool: asyncpg.Pool) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        await conn.execute(sql)
    from app.storage.postgres.migrate_topic_dialog_links import migrate_topic_dialog_links_from_settings

    await migrate_topic_dialog_links_from_settings(pool)


def dumps_state_data(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


def loads_state_data(raw: str | dict) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    return json.loads(raw)
