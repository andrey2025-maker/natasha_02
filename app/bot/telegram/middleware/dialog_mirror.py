from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.dialog_mirror_scheduler import DialogMirrorScheduler
from app.bot.telegram.handlers.questions_topic import (
    DialogMirrorResult,
    forward_idle_message_to_questions_topic,
    forward_message_to_dialog_topic,
    should_forward_idle_message_to_questions,
)
from app.bot.telegram.mirror_bot import DialogMirrorBot, reset_callback_handler_flag, set_callback_handler_flag
from app.core.container import AppContainer
from app.services.admin_tools_service import (
    GroupTopicsStore,
    NotificationSettingsStore,
    QuestionsAlertStore,
    TopicDialogStore,
)


class DialogMirrorIncomingMiddleware(BaseMiddleware):
    """Копирует входящие личные сообщения в тему диалога и алерт в тему «вопросы»."""

    def __init__(self, container: AppContainer, mirror_scheduler: DialogMirrorScheduler) -> None:
        self._container = container
        self._mirror_scheduler = mirror_scheduler
        dsn = container.settings.database.dsn
        self._group_topics_store = GroupTopicsStore(dsn)
        self._notification_settings_store = NotificationSettingsStore(dsn)
        self._topic_dialog_store = TopicDialogStore(dsn)
        self._questions_alert_store = QuestionsAlertStore(dsn)
        self._callback_codec = CallbackCodec(container.callback_signer)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        mirror_task: asyncio.Task[DialogMirrorResult | None] | None = None

        if isinstance(event, Message):
            mirror_task = self._schedule_incoming_mirror(event)

        result = await handler(event, data)

        if isinstance(event, Message) and mirror_task is not None:
            await self._schedule_idle_questions_forward(event, mirror_task)

        return result

    def _schedule_incoming_mirror(
        self,
        message: Message,
    ) -> asyncio.Task[DialogMirrorResult | None] | None:
        if message.chat.type != "private":
            return None
        if not message.from_user or message.from_user.is_bot:
            return None

        chat_id = int(message.chat.id)

        async def mirror_incoming() -> DialogMirrorResult | None:
            return await forward_message_to_dialog_topic(
                message,
                container=self._container,
                group_topics_store=self._group_topics_store,
                notification_settings_store=self._notification_settings_store,
                topic_dialog_store=self._topic_dialog_store,
            )

        return self._mirror_scheduler.submit(chat_id, mirror_incoming, label="incoming")

    async def _schedule_idle_questions_forward(
        self,
        message: Message,
        mirror_task: asyncio.Task[DialogMirrorResult | None],
    ) -> None:
        if not await should_forward_idle_message_to_questions(message, container=self._container):
            return

        chat_id = int(message.chat.id)

        async def forward_questions() -> bool:
            dialog_mirror = await mirror_task
            return await forward_idle_message_to_questions_topic(
                message,
                container=self._container,
                group_topics_store=self._group_topics_store,
                notification_settings_store=self._notification_settings_store,
                topic_dialog_store=self._topic_dialog_store,
                questions_alert_store=self._questions_alert_store,
                callback_codec=self._callback_codec,
                send_ack=True,
                dialog_mirror=dialog_mirror,
            )

        self._mirror_scheduler.submit(chat_id, forward_questions, label="questions_idle")


class DialogMirrorCallbackAfterMiddleware(BaseMiddleware):
    """После нажатия inline-кнопки копирует обновлённую панель бота в тему диалога."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        token = set_callback_handler_flag(True)
        try:
            result = await handler(event, data)
            if isinstance(event, CallbackQuery) and event.message and event.message.chat.type == "private":
                bot = event.message.bot
                if isinstance(bot, DialogMirrorBot):
                    bot.schedule_outgoing_mirror(
                        int(event.message.chat.id),
                        message_id=int(event.message.message_id),
                    )
            return result
        finally:
            reset_callback_handler_flag(token)
