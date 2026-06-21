from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.types import Message

from app.bot.telegram.handlers.questions_topic import mirror_bot_message_to_dialog_topic
from app.core.container import AppContainer
from app.services.admin_tools_service import GroupTopicsStore, NotificationSettingsStore, TopicDialogStore

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


class DialogMirrorBot(Bot):
    """Bot, который дублирует исходящие сообщения в личные чаты в тему диалога."""

    def __init__(self, *, container: AppContainer, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        dsn = container.settings.database.dsn
        self._mirror_container = container
        self._group_topics_store = GroupTopicsStore(dsn)
        self._notification_settings_store = NotificationSettingsStore(dsn)
        self._topic_dialog_store = TopicDialogStore(dsn)

    async def send_media_group(
        self,
        chat_id: int | str,
        media: list[Any],
        **kwargs: Any,
    ) -> list[Message]:
        messages = await super().send_media_group(chat_id, media, **kwargs)
        for sent in messages:
            await self._mirror_outgoing(chat_id, sent)
        return messages

    async def _mirror_outgoing(self, chat_id: int | str, sent: Message | None) -> None:
        if sent is None:
            return
        try:
            target_chat_id = int(chat_id)
        except (TypeError, ValueError):
            return
        if target_chat_id <= 0:
            return
        await mirror_bot_message_to_dialog_topic(
            self,
            user_chat_id=target_chat_id,
            message_id=int(sent.message_id),
            container=self._mirror_container,
            group_topics_store=self._group_topics_store,
            notification_settings_store=self._notification_settings_store,
            topic_dialog_store=self._topic_dialog_store,
        )


def _wrap_outgoing_method(method_name: str) -> None:
    original = getattr(DialogMirrorBot.__mro__[1], method_name)

    async def wrapped(self: DialogMirrorBot, chat_id: int | str, /, *args: Any, **kwargs: Any) -> Any:
        result = await original(self, chat_id, *args, **kwargs)
        await self._mirror_outgoing(chat_id, result if isinstance(result, Message) else None)
        return result

    setattr(DialogMirrorBot, method_name, wrapped)


for _method_name in _OUTGOING_CHAT_ID_METHODS:
    _wrap_outgoing_method(_method_name)
