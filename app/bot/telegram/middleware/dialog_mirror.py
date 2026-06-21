from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from app.bot.telegram.handlers.questions_topic import forward_message_to_dialog_topic
from app.core.container import AppContainer
from app.services.admin_tools_service import GroupTopicsStore, NotificationSettingsStore, TopicDialogStore

DialogMirrorResult = tuple[int, int, int]


class DialogMirrorMiddleware(BaseMiddleware):
    """Копирует каждое входящее личное сообщение пользователя в его тему диалога."""

    def __init__(self, container: AppContainer) -> None:
        self._container = container
        dsn = container.settings.database.dsn
        self._group_topics_store = GroupTopicsStore(dsn)
        self._notification_settings_store = NotificationSettingsStore(dsn)
        self._topic_dialog_store = TopicDialogStore(dsn)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            copied = await self._mirror_incoming(event)
            if copied is not None:
                data["dialog_mirror"] = copied
        return await handler(event, data)

    async def _mirror_incoming(self, message: Message) -> DialogMirrorResult | None:
        if message.chat.type != "private":
            return None
        if not message.from_user or message.from_user.is_bot:
            return None
        return await forward_message_to_dialog_topic(
            message,
            container=self._container,
            group_topics_store=self._group_topics_store,
            notification_settings_store=self._notification_settings_store,
            topic_dialog_store=self._topic_dialog_store,
        )
