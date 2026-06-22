from __future__ import annotations

from app.domain.enums import DialogState

BUYOUT_TEXT_STATES: frozenset[DialogState] = frozenset(
    {
        DialogState.BUYOUT_WAIT_LINK,
        DialogState.BUYOUT_WAIT_DETAILS,
        DialogState.BUYOUT_ADD_MORE,
    }
)

BUYOUT_MEDIA_STATES: frozenset[DialogState] = frozenset({DialogState.BUYOUT_WAIT_MEDIA})

BUYOUT_ALL_STATES: frozenset[DialogState] = BUYOUT_TEXT_STATES | BUYOUT_MEDIA_STATES

TRACK_INPUT_STATES: frozenset[DialogState] = frozenset(
    {
        DialogState.TRACK_WAIT_INPUT,
        DialogState.TRACK_WAIT_CONTINUE,
    }
)

PROFILE_FSM_INPUT_STATES: frozenset[DialogState] = frozenset(
    {
        DialogState.PROFILE_FILL_NAME,
        DialogState.PROFILE_FILL_PHONE,
        DialogState.PROFILE_FILL_CITY,
        DialogState.PROFILE_CONFIRM,
        DialogState.PROFILE_EDIT_NAME,
        DialogState.PROFILE_EDIT_PHONE,
        DialogState.PROFILE_EDIT_CITY,
        DialogState.PROFILE_ASK_HAS_CODE,
        DialogState.PROFILE_ENTER_CODE,
        DialogState.PROFILE_CONFIRM_CODE,
        DialogState.PROFILE_ASK_PASSPORT,
        DialogState.SYNC_ENTER_CODE,
        DialogState.SYNC_VERIFY,
    }
)

PROFILE_AND_TRACK_INPUT_STATES: frozenset[DialogState] = PROFILE_FSM_INPUT_STATES | TRACK_INPUT_STATES
