from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

from app.domain.enums import DialogState, Platform
from app.domain.models import UserProfile, UserSession
from app.services.profile_code_service import ProfileCodeService, UnknownProfileCodeError
from app.services.sync_service import (
    SyncBlockedError,
    SyncCodeMismatchError,
    SyncCooldownError,
    SyncExpiredError,
    SyncService,
)
from app.storage.interfaces import SessionRepository, UserProfileRepository
from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.fsm_utils import fsm_prompt
from app.bot.texts import messages as msg


@dataclass(slots=True)
class FlowResponse:
    text: str
    state: DialogState
    state_data: dict[str, Any]
    reply_markup: Any = None
    profile: Optional[UserProfile] = None
    outbound_messages: list[dict[str, Any]] = field(default_factory=list)


class ProfileFlowService:
    def __init__(
        self,
        profile_repo: UserProfileRepository,
        session_repo: SessionRepository,
        code_service: ProfileCodeService,
        sync_service: SyncService,
    ) -> None:
        self._profiles = profile_repo
        self._sessions = session_repo
        self._codes = code_service
        self._sync_service = sync_service

    async def get_or_create_session(
        self,
        platform: Platform,
        platform_user_id: int,
        *,
        known_profile: UserProfile | None = None,
    ) -> UserSession:
        profile = known_profile
        if profile is None:
            profile = await self._profiles.get_by_platform_user(platform, platform_user_id)
        if profile:
            asyncio.create_task(
                _touch_profile_activity(
                    self._profiles,
                    profile.id,
                    clear_blocked_bot=bool(profile.blocked_bot),
                )
            )
        session = await self._sessions.get(platform, platform_user_id)
        if session:
            return session
        session = UserSession(
            id=0,
            platform=platform,
            platform_user_id=platform_user_id,
            state=DialogState.IDLE,
        )
        return await self._sessions.save(session)

    async def ensure_session(
        self,
        platform: Platform,
        platform_user_id: int,
        *,
        known_profile: UserProfile | None = None,
        existing_session: UserSession | None = None,
    ) -> UserSession:
        session = existing_session
        if session is None:
            session = await self._sessions.get(platform, platform_user_id)
        if session is None:
            return await self.get_or_create_session(
                platform,
                platform_user_id,
                known_profile=known_profile,
            )
        profile = known_profile
        if profile is None:
            profile = await self._profiles.get_by_platform_user(platform, platform_user_id)
        if profile:
            asyncio.create_task(
                _touch_profile_activity(
                    self._profiles,
                    profile.id,
                    clear_blocked_bot=bool(profile.blocked_bot),
                )
            )
        return session

    async def cancel_to_idle(self, session: UserSession) -> None:
        session.state = DialogState.IDLE
        session.state_data = _keep_prefs(session.state_data, {})
        await self._sessions.save(session)

    async def persist_idle_menu_state(self, session: UserSession) -> None:
        new_state_data = _keep_prefs(session.state_data, {})
        if session.state == DialogState.IDLE and session.state_data == new_state_data:
            return
        session.state = DialogState.IDLE
        session.state_data = new_state_data
        await self._sessions.save(session)

    async def show_profile_menu(
        self,
        session: UserSession,
        other_platform_label: str,
        *,
        profile: UserProfile | None = None,
    ) -> FlowResponse:
        if profile is None:
            profile = await self._profiles.get_by_platform_user(session.platform, session.platform_user_id)
        if profile and profile.is_filled:
            text = msg.profile_summary(profile)
        else:
            text = msg.profile_intro()

        new_state_data = _keep_prefs(session.state_data, {})
        if session.state == DialogState.IDLE and session.state_data == new_state_data:
            return FlowResponse(
                text=text,
                state=DialogState.IDLE,
                state_data={},
                profile=profile,
            )

        session.state = DialogState.IDLE
        session.state_data = new_state_data
        await self._sessions.save(session)

        return FlowResponse(
            text=text,
            state=DialogState.IDLE,
            state_data={},
            profile=profile,
        )

    async def start_fill(self, session: UserSession) -> FlowResponse:
        session.state = DialogState.PROFILE_FILL_NAME
        session.state_data = _keep_prefs(session.state_data, {})
        await self._sessions.save(session)
        return FlowResponse(text=fsm_prompt(msg.ask_name()), state=session.state, state_data=session.state_data)

    async def start_sync_with_other_platform(self, session: UserSession) -> FlowResponse:
        session.state = DialogState.SYNC_ENTER_CODE
        session.state_data = _keep_prefs(session.state_data, {})
        await self._sessions.save(session)
        return FlowResponse(
            text=fsm_prompt(msg.sync_enter_profile_code()),
            state=session.state,
            state_data=session.state_data,
        )

    async def handle_text(self, session: UserSession, text: str, callback_codec: CallbackCodec | None = None) -> FlowResponse:
        state = session.state
        data = dict(session.state_data)

        if state == DialogState.SYNC_ENTER_CODE:
            profile_code = text.strip()
            profile = await self._profiles.get_by_code(profile_code)
            if not profile:
                return FlowResponse(text=msg.code_not_found(), state=session.state, state_data=data)
            if session.platform == Platform.TELEGRAM and not profile.vk_user_id:
                return FlowResponse(text=msg.sync_vk_profile_missing(), state=session.state, state_data=data)
            if session.platform == Platform.VK and not profile.telegram_user_id:
                return FlowResponse(text=msg.sync_tg_profile_missing(), state=session.state, state_data=data)

            target_platform = Platform.VK if session.platform == Platform.TELEGRAM else Platform.TELEGRAM
            try:
                request = await self._sync_service.create_sync_request(
                    profile_code=profile_code,
                    from_platform=session.platform,
                    to_platform=target_platform,
                )
            except SyncBlockedError:
                return FlowResponse(text=msg.sync_temporarily_blocked(), state=session.state, state_data=data)
            except SyncCooldownError:
                return FlowResponse(text=msg.sync_cooldown(), state=session.state, state_data=data)
            session.state = DialogState.SYNC_VERIFY
            session.state_data = {
                "profile_code": profile_code,
                "sync_request_id": request.id,
            }
            await self._sessions.save(session)
            target_user_id = profile.vk_user_id if target_platform == Platform.VK else profile.telegram_user_id
            outbound_messages: list[dict[str, Any]] = []
            if target_user_id:
                outbound_messages.append(
                    {
                        "platform": target_platform.value,
                        "platform_user_id": int(target_user_id),
                        "message_type": "sync_code",
                        "payload": {
                            "code": request.verification_code,
                            "profile_code": profile_code,
                            "from_platform": session.platform.value,
                        },
                    }
                )
            return FlowResponse(
                text=msg.sync_code_sent(),
                state=session.state,
                state_data=dict(session.state_data),
                outbound_messages=outbound_messages,
            )

        if state == DialogState.SYNC_VERIFY:
            profile_code = str(data.get("profile_code", "")).strip()
            if not profile_code:
                return FlowResponse(text=msg.unknown_state(), state=DialogState.IDLE, state_data={})
            sync_request = await self._sync_service.get_active_request(profile_code)
            if not sync_request:
                return FlowResponse(text=msg.sync_request_not_found(), state=session.state, state_data=data)
            try:
                await self._sync_service.verify_sync_code(sync_request, text.strip())
            except SyncCodeMismatchError:
                return FlowResponse(text=msg.sync_code_invalid(), state=session.state, state_data=data)
            except SyncExpiredError:
                return FlowResponse(text=msg.sync_code_expired(), state=session.state, state_data=data)

            profile = await self._profiles.get_by_code(profile_code)
            if not profile:
                return FlowResponse(text=msg.code_not_found(), state=session.state, state_data=data)

            if session.platform == Platform.TELEGRAM:
                profile.telegram_user_id = session.platform_user_id
            else:
                profile.vk_user_id = session.platform_user_id
            profile = await self._profiles.save(profile)
            session.state = DialogState.IDLE
            session.state_data = _keep_prefs(session.state_data, {})
            session.user_profile_id = profile.id
            await self._sessions.save(session)
            return FlowResponse(
                text=msg.sync_done(profile),
                state=session.state,
                state_data={},
                profile=profile,
            )

        if state == DialogState.PROFILE_FILL_NAME:
            data["name"] = text.strip()
            session.state = DialogState.PROFILE_FILL_PHONE
            session.state_data = data
            await self._sessions.save(session)
            return FlowResponse(text=fsm_prompt(msg.ask_phone(data["name"])), state=session.state, state_data=data)

        if state == DialogState.PROFILE_FILL_PHONE:
            data["phone"] = text.strip()
            session.state = DialogState.PROFILE_FILL_CITY
            session.state_data = data
            await self._sessions.save(session)
            return FlowResponse(text=fsm_prompt(msg.ask_city(data["name"], data["phone"])), state=session.state, state_data=data)

        if state == DialogState.PROFILE_FILL_CITY:
            data["city"] = text.strip()
            session.state = DialogState.PROFILE_CONFIRM
            session.state_data = data
            await self._sessions.save(session)
            from app.bot.telegram.keyboards.profile import profile_confirm_keyboard

            return FlowResponse(
                text=msg.confirm_profile(data["name"], data["phone"], data["city"]),
                state=session.state,
                state_data=data,
                reply_markup=(
                    profile_confirm_keyboard(session.platform_user_id, callback_codec)
                    if callback_codec is not None
                    else None
                ),
            )

        if state == DialogState.PROFILE_EDIT_NAME:
            data["name"] = text.strip()
            session.state = DialogState.PROFILE_CONFIRM
            session.state_data = data
            await self._sessions.save(session)
            from app.bot.telegram.keyboards.profile import profile_confirm_keyboard

            return FlowResponse(
                text=msg.confirm_profile(data["name"], data["phone"], data["city"]),
                state=session.state,
                state_data=data,
                reply_markup=(
                    profile_confirm_keyboard(session.platform_user_id, callback_codec)
                    if callback_codec is not None
                    else None
                ),
            )

        if state == DialogState.PROFILE_EDIT_PHONE:
            data["phone"] = text.strip()
            session.state = DialogState.PROFILE_CONFIRM
            session.state_data = data
            await self._sessions.save(session)
            from app.bot.telegram.keyboards.profile import profile_confirm_keyboard

            return FlowResponse(
                text=msg.confirm_profile(data["name"], data["phone"], data["city"]),
                state=session.state,
                state_data=data,
                reply_markup=(
                    profile_confirm_keyboard(session.platform_user_id, callback_codec)
                    if callback_codec is not None
                    else None
                ),
            )

        if state == DialogState.PROFILE_EDIT_CITY:
            data["city"] = text.strip()
            session.state = DialogState.PROFILE_CONFIRM
            session.state_data = data
            await self._sessions.save(session)
            from app.bot.telegram.keyboards.profile import profile_confirm_keyboard

            return FlowResponse(
                text=msg.confirm_profile(data["name"], data["phone"], data["city"]),
                state=session.state,
                state_data=data,
                reply_markup=(
                    profile_confirm_keyboard(session.platform_user_id, callback_codec)
                    if callback_codec is not None
                    else None
                ),
            )

        if state == DialogState.PROFILE_ENTER_CODE:
            code = text.strip()
            try:
                await self._codes.validate_manual_code(code)
            except UnknownProfileCodeError:
                return FlowResponse(text=msg.code_not_found(), state=session.state, state_data=data)
            data["code"] = code
            session.state = DialogState.PROFILE_CONFIRM_CODE
            session.state_data = data
            await self._sessions.save(session)
            from app.bot.telegram.keyboards.profile import yes_no_keyboard

            if callback_codec is None:
                return FlowResponse(text=msg.unknown_state(), state=DialogState.IDLE, state_data={})

            return FlowResponse(
                text=msg.confirm_code(code),
                state=session.state,
                state_data=data,
                reply_markup=yes_no_keyboard(
                    "code_confirm",
                    "code_fix",
                    session.platform_user_id,
                    callback_codec,
                ),
            )

        return FlowResponse(text=msg.unknown_state(), state=DialogState.IDLE, state_data={})

    async def handle_callback(self, session: UserSession, action: str, callback_codec: CallbackCodec) -> FlowResponse:
        data = dict(session.state_data)

        if action == "edit_name":
            session.state = DialogState.PROFILE_EDIT_NAME
            await self._sessions.save(session)
            return FlowResponse(text=fsm_prompt(msg.ask_name()), state=session.state, state_data=data)

        if action == "edit_phone":
            session.state = DialogState.PROFILE_EDIT_PHONE
            await self._sessions.save(session)
            return FlowResponse(text=fsm_prompt(msg.ask_phone(data.get("name", ""))), state=session.state, state_data=data)

        if action == "edit_city":
            session.state = DialogState.PROFILE_EDIT_CITY
            await self._sessions.save(session)
            return FlowResponse(
                text=fsm_prompt(msg.ask_city(data.get("name", ""), data.get("phone", ""))),
                state=session.state,
                state_data=data,
            )

        if action == "confirm_yes":
            session.state = DialogState.PROFILE_ASK_HAS_CODE
            await self._sessions.save(session)
            from app.bot.telegram.keyboards.profile import yes_no_keyboard

            return FlowResponse(
                text=msg.ask_has_code(),
                state=session.state,
                state_data=data,
                reply_markup=yes_no_keyboard(
                    "has_code_yes",
                    "has_code_no",
                    session.platform_user_id,
                    callback_codec,
                ),
            )

        if action == "has_code_yes":
            session.state = DialogState.PROFILE_ENTER_CODE
            await self._sessions.save(session)
            return FlowResponse(text=fsm_prompt(msg.enter_existing_code()), state=session.state, state_data=data)

        if action == "has_code_no":
            code = await self._codes.allocate_for_new_user()
            data["code"] = code
            session.state = DialogState.PROFILE_ASK_PASSPORT
            session.state_data = data
            await self._sessions.save(session)
            from app.bot.telegram.keyboards.profile import yes_no_keyboard

            return FlowResponse(
                text=msg.ask_passport(code),
                state=session.state,
                state_data=data,
                reply_markup=yes_no_keyboard(
                    "passport_yes",
                    "passport_no",
                    session.platform_user_id,
                    callback_codec,
                ),
            )

        if action == "code_confirm":
            session.state = DialogState.PROFILE_ASK_PASSPORT
            await self._sessions.save(session)
            from app.bot.telegram.keyboards.profile import yes_no_keyboard

            return FlowResponse(
                text=msg.ask_passport(data["code"]),
                state=session.state,
                state_data=data,
                reply_markup=yes_no_keyboard(
                    "passport_yes",
                    "passport_no",
                    session.platform_user_id,
                    callback_codec,
                ),
            )

        if action == "code_fix":
            session.state = DialogState.PROFILE_ENTER_CODE
            await self._sessions.save(session)
            return FlowResponse(text=fsm_prompt(msg.enter_existing_code()), state=session.state, state_data=data)

        if action in {"passport_yes", "passport_no"}:
            data["has_passport"] = action == "passport_yes"
            profile = await self._finalize_profile(session, data)
            session.state = DialogState.IDLE
            session.state_data = _keep_prefs(session.state_data, {})
            session.user_profile_id = profile.id
            await self._sessions.save(session)
            return FlowResponse(
                text=msg.platforms_text(),
                state=DialogState.IDLE,
                state_data={},
                profile=profile,
            )

        return FlowResponse(text=msg.unknown_state(), state=DialogState.IDLE, state_data={})

    async def _finalize_profile(self, session: UserSession, data: dict[str, Any]) -> UserProfile:
        existing = await self._profiles.get_by_platform_user(session.platform, session.platform_user_id)
        profile = existing or UserProfile(
            id=0,
            code=data["code"],
            name=data["name"],
            phone=data["phone"],
            city=data["city"],
            has_passport=bool(data.get("has_passport")),
        )
        profile.code = data["code"]
        profile.name = data["name"]
        profile.phone = data["phone"]
        profile.city = data["city"]
        profile.has_passport = bool(data.get("has_passport"))
        if session.platform == Platform.TELEGRAM:
            profile.telegram_user_id = session.platform_user_id
        else:
            profile.vk_user_id = session.platform_user_id
        return await self._profiles.save(profile)


async def _touch_profile_activity(
    profiles: UserProfileRepository,
    profile_id: int,
    *,
    clear_blocked_bot: bool,
) -> None:
    try:
        profile = await profiles.get_by_id(profile_id)
        if not profile:
            return
        profile.last_activity_at = datetime.utcnow()
        if clear_blocked_bot and profile.blocked_bot:
            profile.blocked_bot = False
        await profiles.save(profile)
    except Exception:
        logger.exception("Failed to touch profile activity for id=%s", profile_id)


def _keep_prefs(existing: dict[str, Any], new_data: dict[str, Any]) -> dict[str, Any]:
    prefs = existing.get("_prefs")
    if isinstance(prefs, dict):
        merged = dict(new_data)
        merged["_prefs"] = dict(prefs)
        return merged
    return dict(new_data)
