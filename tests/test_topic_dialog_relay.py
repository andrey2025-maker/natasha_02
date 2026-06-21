from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.telegram.handlers.questions import _relay_topic_reply_to_user
from app.domain.enums import Platform
from app.services.admin_tools_service import TopicDialogStore, _MEMORY_SETTINGS


@pytest.fixture(autouse=True)
def clear_memory_settings() -> None:
    _MEMORY_SETTINGS.clear()


@pytest.mark.anyio
async def test_bind_and_resolve_by_topic_message_memory() -> None:
    store = TopicDialogStore("memory")
    chat_id = -1001234567890
    topic_id = 42
    client_message_id = 9001
    user_id = 555001

    await store.bind_topic_message_to_user(
        chat_id=chat_id,
        topic_id=topic_id,
        topic_message_id=client_message_id,
        platform=Platform.TELEGRAM.value,
        platform_user_id=user_id,
    )

    resolved = await store.resolve_user_by_topic_message(
        chat_id=chat_id,
        topic_id=topic_id,
        topic_message_id=client_message_id,
    )
    assert resolved == (Platform.TELEGRAM.value, user_id)


@pytest.mark.anyio
async def test_resolve_by_topic_message_miss_memory() -> None:
    store = TopicDialogStore("memory")
    resolved = await store.resolve_user_by_topic_message(
        chat_id=-1001,
        topic_id=7,
        topic_message_id=999,
    )
    assert resolved is None


@pytest.mark.anyio
async def test_bind_trims_to_500_links_per_topic_memory() -> None:
    store = TopicDialogStore("memory")
    chat_id = -100999
    topic_id = 1

    for message_id in range(1, 502):
        await store.bind_topic_message_to_user(
            chat_id=chat_id,
            topic_id=topic_id,
            topic_message_id=message_id,
            platform=Platform.TELEGRAM.value,
            platform_user_id=1000 + message_id,
        )

    assert await store.resolve_user_by_topic_message(chat_id, topic_id, 1) is None
    assert await store.resolve_user_by_topic_message(chat_id, topic_id, 2) == (
        Platform.TELEGRAM.value,
        1002,
    )
    latest = await store.resolve_user_by_topic_message(chat_id, topic_id, 501)
    assert latest == (Platform.TELEGRAM.value, 1501)


@pytest.mark.anyio
async def test_relay_reply_to_bound_client_message() -> None:
    store = TopicDialogStore("memory")
    chat_id = -100777
    topic_id = 15
    client_message_id = 300
    user_id = 424242

    await store.bind_topic_message_to_user(
        chat_id=chat_id,
        topic_id=topic_id,
        topic_message_id=client_message_id,
        platform=Platform.TELEGRAM.value,
        platform_user_id=user_id,
    )

    reply_message = MagicMock()
    reply_message.message_id = 301
    reply_message.text = "Ответ менеджера"
    reply_message.chat.id = chat_id
    reply_message.message_thread_id = topic_id
    reply_message.reply_to_message = SimpleNamespace(message_id=client_message_id)
    reply_message.from_user = SimpleNamespace(id=1)
    reply_message.bot = AsyncMock()

    container = SimpleNamespace(
        profile_repo=SimpleNamespace(
            get_by_platform_user=AsyncMock(return_value=None),
            save=AsyncMock(),
        )
    )

    with patch("app.bot.telegram.handlers.questions.api_send_message", new_callable=AsyncMock) as send_mock:
        with patch(
            "app.bot.telegram.handlers.questions._mark_relay_delivered_in_topic",
            new_callable=AsyncMock,
        ) as reaction_mock:
            relayed = await _relay_topic_reply_to_user(
                message=reply_message,
                container=container,
                topic_dialog_store=store,
                as_media=False,
            )

    assert relayed is True
    send_mock.assert_awaited_once_with(
        reply_message.bot,
        chat_id=user_id,
        text="Ответ менеджера",
        parse_mode=None,
    )
    reaction_mock.assert_awaited_once_with(reply_message)

    manager_bound = await store.resolve_user_by_topic_message(
        chat_id=chat_id,
        topic_id=topic_id,
        topic_message_id=301,
    )
    assert manager_bound == (Platform.TELEGRAM.value, user_id)


@pytest.mark.anyio
async def test_relay_falls_back_to_resolve_user_by_topic() -> None:
    store = TopicDialogStore("memory")
    chat_id = -100888
    topic_id = 77
    user_id = 909090

    await store.set_user_topic(
        chat_id=chat_id,
        platform=Platform.TELEGRAM.value,
        platform_user_id=user_id,
        topic_id=topic_id,
    )

    message = MagicMock()
    message.message_id = 500
    message.text = "Сообщение без reply"
    message.chat.id = chat_id
    message.message_thread_id = topic_id
    message.reply_to_message = None
    message.from_user = SimpleNamespace(id=1)
    message.bot = AsyncMock()

    container = SimpleNamespace(
        profile_repo=SimpleNamespace(
            get_by_platform_user=AsyncMock(return_value=None),
            save=AsyncMock(),
        )
    )

    with patch("app.bot.telegram.handlers.questions.api_send_message", new_callable=AsyncMock) as send_mock:
        with patch("app.bot.telegram.handlers.questions._mark_relay_delivered_in_topic", new_callable=AsyncMock):
            relayed = await _relay_topic_reply_to_user(
                message=message,
                container=container,
                topic_dialog_store=store,
                as_media=False,
            )

    assert relayed is True
    send_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_postgres_bind_and_resolve_use_table() -> None:
    captured: dict[str, object] = {"execute_calls": [], "fetchrow_result": None}

    class FakeTransaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeConnection:
        def transaction(self):
            return FakeTransaction()

        async def execute(self, query, *args):
            captured["execute_calls"].append((query.strip(), args))

        async def fetchrow(self, query, *args):
            captured["fetchrow_query"] = query.strip()
            captured["fetchrow_args"] = args
            return captured.get("fetchrow_result")

    class FakeAcquire:
        def __init__(self, conn: FakeConnection) -> None:
            self._conn = conn

        async def __aenter__(self) -> FakeConnection:
            return self._conn

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class FakePool:
        def acquire(self) -> FakeAcquire:
            return FakeAcquire(FakeConnection())

    with patch(
        "app.services.admin_tools_service.DbSettingsStore._pool",
        new=AsyncMock(return_value=FakePool()),
    ):
        store = TopicDialogStore("postgresql://example")
        await store.bind_topic_message_to_user(
            chat_id=-1001,
            topic_id=33,
            topic_message_id=100,
            platform=Platform.TELEGRAM.value,
            platform_user_id=123,
        )

        execute_calls = captured["execute_calls"]
        assert len(execute_calls) == 2
        assert "INSERT INTO topic_dialog_message_links" in execute_calls[0][0]
        assert execute_calls[0][1] == (-1001, 33, 100, Platform.TELEGRAM.value, 123)
        assert "DELETE FROM topic_dialog_message_links" in execute_calls[1][0]

        captured["fetchrow_result"] = {
            "platform": Platform.TELEGRAM.value,
            "platform_user_id": 123,
        }
        resolved = await store.resolve_user_by_topic_message(
            chat_id=-1001,
            topic_id=33,
            topic_message_id=100,
        )

    assert resolved == (Platform.TELEGRAM.value, 123)
    assert "FROM topic_dialog_message_links" in captured["fetchrow_query"]
