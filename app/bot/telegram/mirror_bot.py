from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from aiogram import Bot
from aiogram.types import Message, MessageId

from app.bot.telegram.dialog_mirror_scheduler import DialogMirrorScheduler
from app.bot.telegram.handlers.questions_topic import mirror_bot_message_to_dialog_topic
from app.core.container import AppContainer
from app.services.admin_tools_service import GroupTopicsStore, NotificationSettingsStore, TopicDialogStore

_dialog_mirror_skip: ContextVar[bool] = ContextVar("dialog_mirror_skip", default=False)
_in_callback_handler: ContextVar[bool] = ContextVar("dialog_mirror_in_callback", default=False)

_OUTGOING_CHAT_ID_METHODS = (
    "send_message",
    "send_photo",
    "send_video",
    "send_animation",
    "send_audio",
    "send_document",
    "send_sticker",
    "send_video_note",
    "send_voice",
    "send_location",
    "send_venue",
    "send_contact",
    "send_poll",
    "send_dice",
    "copy_message",
    "forward_message",
)


def dialog_mirror_skipped() -> bool:
    return _dialog_mirror_skip.get()


def in_callback_handler() -> bool:
    return _in_callback_handler.get()


def set_callback_handler_flag(value: bool) -> ContextVar[bool].Token:
    return _in_callback_handler.set(value)


def reset_callback_handler_flag(token: ContextVar[bool].Token) -> None:
    _in_callback_handler.reset(token)


@asynccontextmanager
async def skip_dialog_mirror():
    token = _dialog_mirror_skip.set(True)
    try:
        yield
    finally:
        _dialog_mirror_skip.reset(token)


class DialogMirrorBot(Bot):
    """Bot, который дублирует исходящие сообщения в личные чаты в тему диалога."""

    def __init__(
        self,
        *,
        container: AppContainer,
        mirror_scheduler: DialogMirrorScheduler,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        dsn = container.settings.database.dsn
        self._mirror_container = container
        self._mirror_scheduler = mirror_scheduler
        self._group_topics_store = GroupTopicsStore(dsn)
        self._notification_settings_store = NotificationSettingsStore(dsn)
        self._topic_dialog_store = TopicDialogStore(dsn)

    @property
    def mirror_scheduler(self) -> DialogMirrorScheduler:
        return self._mirror_scheduler

    async def send_media_group(
        self,
        chat_id: int | str,
        media: list[Any],
        **kwargs: Any,
    ) -> list[Message]:
        messages = await super().send_media_group(chat_id, media, **kwargs)
        for sent in messages:
            self._schedule_outgoing_mirror(chat_id, sent)
        return messages

    async def copy_messages(
        self,
        chat_id: int | str,
        from_chat_id: int | str,
        message_ids: list[int],
        **kwargs: Any,
    ) -> list[MessageId]:
        copied = await super().copy_messages(chat_id, from_chat_id, message_ids, **kwargs)
        for item in copied:
            self._schedule_outgoing_mirror(chat_id, item)
        return copied

    def schedule_outgoing_mirror(
        self,
        chat_id: int | str,
        sent: Message | MessageId | None = None,
        *,
        message_id: int | None = None,
    ) -> None:
        self._schedule_outgoing_mirror(chat_id, sent, message_id=message_id)

    def mirror_private_chat_message(self, chat_id: int, message_id: int) -> None:
        self.schedule_outgoing_mirror(chat_id, message_id=message_id)

    def _schedule_outgoing_mirror(
        self,
        chat_id: int | str,
        sent: Message | MessageId | None = None,
        *,
        message_id: int | None = None,
    ) -> None:
        if dialog_mirror_skipped():
            return
        try:
            target_chat_id = int(chat_id)
        except (TypeError, ValueError):
            return
        if target_chat_id <= 0:
            return

        resolved_message_id = message_id
        if resolved_message_id is None and sent is not None:
            resolved_message_id = int(sent.message_id)
        if resolved_message_id is None:
            return

        resolved_id = int(resolved_message_id)

        async def mirror_outgoing() -> None:
            await mirror_bot_message_to_dialog_topic(
                self,
                user_chat_id=target_chat_id,
                message_id=resolved_id,
                container=self._mirror_container,
                group_topics_store=self._group_topics_store,
                notification_settings_store=self._notification_settings_store,
                topic_dialog_store=self._topic_dialog_store,
            )

        self._mirror_scheduler.submit_fire_and_forget(
            target_chat_id,
            mirror_outgoing,
            label="outgoing",
        )


def _wrap_outgoing_method(method_name: str) -> None:
    original = getattr(DialogMirrorBot.__mro__[1], method_name)

    async def wrapped(self: DialogMirrorBot, *args: Any, **kwargs: Any) -> Any:
        if args:
            chat_id = args[0]
            result = await original(self, *args, **kwargs)
        else:
            chat_id = kwargs.get("chat_id")
            result = await original(self, **kwargs)
        if chat_id is not None:
            self._schedule_outgoing_mirror(
                chat_id,
                result if isinstance(result, (Message, MessageId)) else None,
            )
        return result

    setattr(DialogMirrorBot, method_name, wrapped)


for _method_name in _OUTGOING_CHAT_ID_METHODS:
    _wrap_outgoing_method(_method_name)
