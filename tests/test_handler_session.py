from __future__ import annotations

import pytest

from app.bot.telegram.dialog_state_groups import (
    BUYOUT_MEDIA_STATES,
    BUYOUT_TEXT_STATES,
    PROFILE_FSM_INPUT_STATES,
)
from app.bot.telegram.filters.dialog_state import DialogStatesFilter
from app.bot.telegram.handler_session import USER_SESSION_DATA_KEY, handler_data, resolve_user_session
from app.domain.enums import DialogState, Platform
from app.domain.models import UserSession


@pytest.mark.anyio
async def test_dialog_states_filter_uses_preloaded_session() -> None:
    filt = DialogStatesFilter(*BUYOUT_TEXT_STATES)
    session = UserSession(
        id=1,
        platform=Platform.TELEGRAM,
        platform_user_id=42,
        state=DialogState.BUYOUT_WAIT_LINK,
    )
    assert await filt(user_session=session) is True
    assert await filt(user_session=None) is False

    idle = UserSession(
        id=1,
        platform=Platform.TELEGRAM,
        platform_user_id=42,
        state=DialogState.IDLE,
    )
    assert await filt(user_session=idle) is False


@pytest.mark.anyio
async def test_dialog_states_filter_accepts_event_positional_arg() -> None:
    filt = DialogStatesFilter(*BUYOUT_MEDIA_STATES)
    session = UserSession(
        id=1,
        platform=Platform.TELEGRAM,
        platform_user_id=42,
        state=DialogState.BUYOUT_WAIT_MEDIA,
    )
    event = object()
    assert await filt(event, user_session=session) is True
    assert await filt(event, user_session=None) is False


@pytest.mark.anyio
async def test_dialog_states_filter_profile_states() -> None:
    filt = DialogStatesFilter(*PROFILE_FSM_INPUT_STATES)
    session = UserSession(
        id=1,
        platform=Platform.TELEGRAM,
        platform_user_id=7,
        state=DialogState.PROFILE_FILL_NAME,
    )
    assert await filt(user_session=session) is True


@pytest.mark.anyio
async def test_resolve_user_session_reuses_preloaded() -> None:
    class _Repo:
        def __init__(self) -> None:
            self.calls = 0

        async def get(self, platform: Platform, user_id: int) -> UserSession | None:
            self.calls += 1
            return None

    class _Flow:
        def __init__(self) -> None:
            self.calls = 0

        async def get_or_create_session(self, platform, user_id, *, known_profile=None):
            self.calls += 1
            return UserSession(
                id=2,
                platform=platform,
                platform_user_id=user_id,
                state=DialogState.IDLE,
            )

    class _Container:
        def __init__(self) -> None:
            self.session_repo = _Repo()
            self.profile_flow = _Flow()

    container = _Container()
    preloaded = UserSession(
        id=9,
        platform=Platform.TELEGRAM,
        platform_user_id=100,
        state=DialogState.BUYOUT_WAIT_MEDIA,
    )
    data = handler_data(preloaded)
    session = await resolve_user_session(data, container, Platform.TELEGRAM, 100)
    assert session is preloaded
    assert container.session_repo.calls == 0
    assert container.profile_flow.calls == 0
    assert data[USER_SESSION_DATA_KEY] is preloaded
