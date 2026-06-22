from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from app.bot.telegram.handler_session import preload_user_session
from app.core.container import AppContainer
from app.domain.enums import DialogState, Platform
from app.domain.models import UserSession


class DialogStatesFilter(BaseFilter):
    """Пропускает update только если сессия в нужном DialogState (lazy load при проверке FSM)."""

    def __init__(self, *states: DialogState, container: AppContainer | None = None) -> None:
        self.states = frozenset(states)
        self._container = container

    async def __call__(self, *args: Any, **data: Any) -> bool:
        session = data.get("user_session")
        if session is None and self._container is not None:
            event: TelegramObject | None = args[0] if args else None
            user_id = _user_id_from_event(event, data)
            if user_id is not None:
                session = await preload_user_session(
                    data,
                    self._container,
                    Platform.TELEGRAM,
                    user_id,
                )
        return isinstance(session, UserSession) and session.state in self.states


def _user_id_from_event(event: TelegramObject | None, data: dict[str, Any]) -> int | None:
    from aiogram.types import CallbackQuery, Message, User

    if isinstance(event, Message) and event.from_user:
        return int(event.from_user.id)
    if isinstance(event, CallbackQuery) and event.from_user:
        return int(event.from_user.id)
    if event is not None:
        from_user = getattr(event, "from_user", None)
        if from_user is not None and getattr(from_user, "id", None) is not None:
            return int(from_user.id)
    event_user = data.get("event_from_user")
    if isinstance(event_user, User):
        return int(event_user.id)
    return None
