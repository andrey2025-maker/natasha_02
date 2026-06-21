from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.handlers.questions_topic import (
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

DialogMirrorResult = tuple[int, int, int]


class DialogMirrorIncomingMiddleware(BaseMiddleware):
    """Копирует входящие личные сообщения в тему диалога и алерт в тему «вопросы»."""

    def __init__(self, container: AppContainer) -> None:
        self._container = container
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
        if isinstance(event, Message):
            copied = await self._mirror_incoming(event)
            if copied is not None:
                data["dialog_mirror"] = copied

        result = await handler(event, data)

        if isinstance(event, Message) and not data.get("questions_alert_sent"):
            await self._maybe_forward_idle_to_questions(event, data)

        return result

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

    async def _maybe_forward_idle_to_questions(self, message: Message, data: dict[str, Any]) -> None:
        if not await should_forward_idle_message_to_questions(message, container=self._container):
            return
        sent = await forward_idle_message_to_questions_topic(
            message,
            container=self._container,
            group_topics_store=self._group_topics_store,
            notification_settings_store=self._notification_settings_store,
            topic_dialog_store=self._topic_dialog_store,
            questions_alert_store=self._questions_alert_store,
            callback_codec=self._callback_codec,
            send_ack=True,
            dialog_mirror=data.get("dialog_mirror"),
        )
        if sent:
            data["questions_alert_sent"] = True


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
                    try:
                        await bot.mirror_private_chat_message(
                            int(event.message.chat.id),
                            int(event.message.message_id),
                        )
                    except Exception:
                        pass
            return result
        finally:
            reset_callback_handler_flag(token)
