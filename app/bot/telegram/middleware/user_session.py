from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot.telegram.handler_session import USER_SESSION_DATA_KEY
from app.core.container import AppContainer
from app.domain.enums import Platform


def _private_user_id(event: TelegramObject) -> int | None:
    if isinstance(event, Message):
        if event.chat.type != "private" or not event.from_user or event.from_user.is_bot:
            return None
        return int(event.from_user.id)
    if isinstance(event, CallbackQuery):
        if not event.from_user or event.from_user.is_bot:
            return None
        message = event.message
        if message is None or message.chat.type != "private":
            return None
        return int(event.from_user.id)
    return None


class UserSessionMiddleware(BaseMiddleware):
    """Предзагружает UserSession один раз на private message/callback."""

    def __init__(self, container: AppContainer) -> None:
        self._container = container

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = _private_user_id(event)
        if user_id is not None:
            session = await self._container.session_repo.get(Platform.TELEGRAM, user_id)
            data[USER_SESSION_DATA_KEY] = session
        return await handler(event, data)
