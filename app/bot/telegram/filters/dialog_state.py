from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter

from app.domain.enums import DialogState
from app.domain.models import UserSession


class DialogStatesFilter(BaseFilter):
    """Пропускает update только если предзагруженная сессия в нужном DialogState."""

    def __init__(self, *states: DialogState) -> None:
        self.states = frozenset(states)

    async def __call__(self, user_session: UserSession | None = None, **_: Any) -> bool:
        return user_session is not None and user_session.state in self.states
