from __future__ import annotations

import pytest

from app.storage.postgres.migrate_topic_dialog_links import _import_legacy_links


@pytest.mark.anyio
async def test_migration_imports_legacy_json_shape() -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.records: list[tuple[int, int, int, str, int]] = []

        async def executemany(self, query, records):
            self.records.extend(records)

    conn = FakeConnection()
    payload = {
        "-100123:42": {
            "1001": {"platform": "telegram", "platform_user_id": 555},
            "1002": {"platform": "telegram", "platform_user_id": 556},
        },
        "bad": "skip",
    }

    inserted = await _import_legacy_links(conn, payload)

    assert inserted == 2
    assert conn.records == [
        (-100123, 42, 1001, "telegram", 555),
        (-100123, 42, 1002, "telegram", 556),
    ]
