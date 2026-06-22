from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter

from app.domain.enums import DialogState
from app.domain.models import UserSession


class DialogStatesFilter(BaseFilter):
    """Пропускает update только если предзагруженная сессия в нужном DialogState."""

    def __init__(self, *states: DialogState) -> None:
        self.states = frozenset(states)

    async def __call__(self, **data: Any) -> bool:
        session = data.get("user_session")
        return isinstance(session, UserSession) and session.state in self.states
