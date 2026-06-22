from __future__ import annotations

import asyncio
import logging
from html import escape

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot.telegram.callbacks import CallbackAuthError, CallbackCodec
from app.bot.telegram.callback_panel import edit_panel_message
from app.bot.telegram.dialog_state_groups import BUYOUT_ALL_STATES, PROFILE_AND_TRACK_INPUT_STATES
from app.bot.telegram.filters.dialog_state import DialogStatesFilter
from app.bot.telegram.handler_session import handler_data, resolve_user_session
from app.bot.telegram.keyboards.main_menu import my_orders_message_keyboard
from app.bot.telegram.keyboards.profile import (
    platforms_keyboard,
    profile_menu_keyboard,
    track_continue_keyboard,
    track_mode_keyboard,
)
from app.bot.telegram.menu_texts import DELEGATED_MENU_TEXTS
from app.bot.telegram.my_orders_media import MY_ORDERS_LOADING_TEXT, open_my_orders_panel
from app.bot.telegram.user_access import is_user_blocked_by_admin
from app.bot.texts import messages as msg
from app.core.container import AppContainer
from app.domain.enums import DialogState, Platform
from app.domain.models import BuyoutOrder, OutboundMessage
from app.services.admin_tools_service import GroupTopicsStore, NotificationSettingsStore, TopicDialogStore
from app.bot.telegram.handlers.admin import admin_session_has_pending, clear_admin_input_states
from app.services.dialog_topic_profile_sync import schedule_refresh_dialog_topic_profile

logger = logging.getLogger(__name__)

PROFILE_BUTTONS = {"Профиль", "👤 Профиль", "Заполнить профиль"}
CONFIRM_BUTTONS = {"Да", "Имя", "Тел.", "Город"}
SYNC_BUTTONS = {"Есть профиль ВК"}
PROFILE_CALLBACK_ACTIONS = {
    "code_confirm",
    "code_fix",
    "has_code_yes",
    "has_code_no",
    "passport_yes",
    "passport_no",
    "edit_name",
    "edit_phone",
    "edit_city",
    "confirm_yes",
    "profile:start_fill",
    "profile:start_sync",
    "profile:buyout_start",
    "profile:buyout_orders",
    "profile:buyout_filters",
    "profile:track:open",
    "profile:track:mode:numbers",
    "profile:track:mode:comments",
    "profile:track:more",
    "profile:track:done",
}


def build_profile_router(container: AppContainer) -> Router:
    router = Router()
    platform = Platform.TELEGRAM
    callback_codec = CallbackCodec(container.callback_signer)
    group_topics_store = GroupTopicsStore(container.settings.database.dsn)
    notification_settings_store = NotificationSettingsStore(container.settings.database.dsn)
    topic_dialog_store = TopicDialogStore(container.settings.database.dsn)

    async def _apply_response(message: Message, response: object, *, edit: bool = False) -> None:
        outbound_messages = getattr(response, "outbound_messages", None)
        if isinstance(outbound_messages, list) and outbound_messages:
            await _dispatch_outbound(message, outbound_messages)

        text = getattr(response, "text", None)
        if not isinstance(text, str):
            profile = getattr(response, "profile", None)
            if profile is not None and getattr(profile, "telegram_user_id", None):
                schedule_refresh_dialog_topic_profile(
                    message.bot,
                    container=container,
                    tg_user_id=int(profile.telegram_user_id),
                    group_topics_store=group_topics_store,
                    topic_dialog_store=topic_dialog_store,
                    notification_settings_store=notification_settings_store,
                )
            return
        reply_markup = getattr(response, "reply_markup", None)
        if edit:
            await edit_panel_message(
                message,
                text=text,
                reply_markup=reply_markup,
            )
        else:
            kwargs = {"parse_mode": "HTML"}
            if reply_markup is not None:
                kwargs["reply_markup"] = reply_markup
            await message.answer(text, **kwargs)
        profile = getattr(response, "profile", None)
        if profile is not None and getattr(profile, "telegram_user_id", None):
            schedule_refresh_dialog_topic_profile(
                message.bot,
                container=container,
                tg_user_id=int(profile.telegram_user_id),
                group_topics_store=group_topics_store,
                topic_dialog_store=topic_dialog_store,
                notification_settings_store=notification_settings_store,
            )

    async def _dispatch_outbound(message: Message, outbound_messages: list[dict]) -> None:
        for outgoing in outbound_messages:
            platform_name = str(outgoing["platform"])
            target_platform = Platform(platform_name)
            target_user_id = int(outgoing["platform_user_id"])
            payload = dict(outgoing["payload"])
            if target_platform == Platform.TELEGRAM:
                try:
                    await message.bot.send_message(
                        chat_id=target_user_id,
                        text=msg.sync_code_for_other_platform(
                            code=str(payload.get("code", "")),
                            profile_code=str(payload.get("profile_code", "")),
                            from_platform=str(payload.get("from_platform", "")),
                        ),
                        parse_mode="HTML",
                    )
                except TelegramForbiddenError:
                    profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, target_user_id)
                    if profile and not profile.blocked_bot:
                        profile.blocked_bot = True
                        await container.profile_repo.save(profile)
                        schedule_refresh_dialog_topic_profile(
                            message.bot,
                            container=container,
                            tg_user_id=target_user_id,
                            group_topics_store=group_topics_store,
                            topic_dialog_store=topic_dialog_store,
                            notification_settings_store=notification_settings_store,
                        )
                continue

            await container.outbound_repo.enqueue(
                OutboundMessage(
                    id=0,
                    platform=target_platform,
                    platform_user_id=target_user_id,
                    message_type=str(outgoing["message_type"]),
                    payload=payload,
                )
            )

    async def _is_blocked_user(user_id: int) -> bool:
        return await is_user_blocked_by_admin(container, user_id)

    _profile_input_state_filter = DialogStatesFilter(*PROFILE_AND_TRACK_INPUT_STATES)
    _not_buyout_media_filter = ~DialogStatesFilter(*BUYOUT_ALL_STATES)

    async def _post_user_tracks_to_group(
        message: Message,
        *,
        profile,
        orders: list[BuyoutOrder],
    ) -> tuple[bool, str]:
        topics = await group_topics_store.ensure_all_system_topics(message.bot)
        if not topics or "buyout" not in topics:
            return False, "Тема «Выкупы» недоступна. Обратитесь в поддержку."
        lines = [
            "<b>📦 Трек-номера</b>",
            f"Клиент: {escape(profile.code)} / {escape(profile.name or '—')}",
            "",
        ]
        for order in orders:
            track = escape(order.track_number or "")
            comment = (order.quantity_text or "").strip()
            if comment == "—":
                comment = ""
            line = f"• <b>{escape(order.order_number)}</b>: <code>{track}</code>"
            if comment:
                line += f" — {escape(comment)}"
            lines.append(line)
        try:
            disable_notification = await notification_settings_store.should_disable_notification("user")
            await message.bot.send_message(
                chat_id=int(topics["chat_id"]),
                text="\n".join(lines),
                parse_mode="HTML",
                message_thread_id=int(topics["buyout"]),
                disable_notification=disable_notification,
            )
        except Exception:
            return False, "Не удалось отправить трек-номера. Попробуйте позже."
        return True, f"Сохранено заказов: {len(orders)}."

    def _track_reply_markup(user_id: int, state: DialogState):
        if state == DialogState.TRACK_WAIT_CONTINUE:
            return track_continue_keyboard(user_id, callback_codec)
        return None

    def _my_orders_reply_markup(user_id: int, session, response) -> InlineKeyboardMarkup:
        state_data = response.state_data if isinstance(getattr(response, "state_data", None), dict) else {}
        filters = container.buyout_flow.filter_states(session)
        return my_orders_message_keyboard(
            user_id=user_id,
            current_page=int(state_data.get("page", 1)),
            total_pages=max(1, int(state_data.get("total_pages", 1))),
            filters=filters,
            codec=callback_codec,
        )

    @router.message(F.text.in_({"Профиль", "👤 Профиль"}))
    async def profile_menu(message: Message) -> None:
        if not message.from_user:
            return
        profile = await container.profile_repo.get_by_platform_user(platform, message.from_user.id)
        if profile and profile.is_blocked_by_admin:
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        if profile and profile.is_filled:
            text = msg.profile_summary(profile)
        else:
            text = msg.profile_intro()
        reply_markup = profile_menu_keyboard(
            "ВК",
            message.from_user.id,
            callback_codec,
            profile=profile,
        )
        await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)

        async def finalize_profile_menu() -> None:
            try:
                if profile is not None and getattr(profile, "telegram_user_id", None):
                    schedule_refresh_dialog_topic_profile(
                        message.bot,
                        container=container,
                        tg_user_id=int(profile.telegram_user_id),
                        group_topics_store=group_topics_store,
                        topic_dialog_store=topic_dialog_store,
                        notification_settings_store=notification_settings_store,
                    )
                existing_session = await container.session_repo.get(platform, message.from_user.id)
                session = await container.profile_flow.ensure_session(
                    platform,
                    message.from_user.id,
                    known_profile=profile,
                    existing_session=existing_session,
                )
                await container.profile_flow.persist_idle_menu_state(session)
                if await container.admin_service.is_admin(message.from_user.id):
                    await clear_admin_input_states(container, session)
            except Exception:
                logger.exception("Failed to finalize profile menu for user_id=%s", message.from_user.id)

        asyncio.create_task(finalize_profile_menu())

    @router.message(F.text == "Заполнить профиль")
    async def start_fill(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        profile = await container.profile_repo.get_by_platform_user(platform, message.from_user.id)
        if profile and profile.is_filled:
            session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
            response = await container.profile_flow.show_profile_menu(session, other_platform_label="ВК")
            response.reply_markup = profile_menu_keyboard("ВК", message.from_user.id, callback_codec, profile=profile)
            await _apply_response(message, response)
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        response = await container.profile_flow.start_fill(session)
        await _apply_response(message, response)

    @router.message(F.text.in_(CONFIRM_BUTTONS))
    async def confirm_buttons(message: Message) -> None:
        if not message.from_user or not message.text:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        if session.state != DialogState.PROFILE_CONFIRM:
            return

        action_map = {
            "Да": "confirm_yes",
            "Имя": "edit_name",
            "Тел.": "edit_phone",
            "Город": "edit_city",
        }
        response = await container.profile_flow.handle_callback(
            session,
            action_map[message.text],
            callback_codec,
        )
        await _apply_response(message, response)

    @router.message(F.text == "Есть профиль ВК")
    async def start_sync(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        profile = await container.profile_repo.get_by_platform_user(platform, message.from_user.id)
        if profile and profile.is_filled:
            session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
            response = await container.profile_flow.show_profile_menu(session, other_platform_label="ВК")
            response.reply_markup = profile_menu_keyboard("ВК", message.from_user.id, callback_codec, profile=profile)
            await _apply_response(message, response)
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        response = await container.profile_flow.start_sync_with_other_platform(session)
        await _apply_response(message, response)

    @router.callback_query()
    async def profile_callbacks(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.data or not callback.message:
            raise SkipHandler
        try:
            action = callback_codec.decode(callback.data, callback.from_user.id)
        except CallbackAuthError:
            raise SkipHandler
        if action not in PROFILE_CALLBACK_ACTIONS:
            raise SkipHandler
        if await _is_blocked_user(callback.from_user.id):
            await callback.answer("Доступ ограничен", show_alert=True)
            return
        if action in {"profile:buyout_orders", "profile:buyout_filters"}:
            await callback.answer()
            loading = await callback.message.bot.send_message(
                chat_id=callback.message.chat.id,
                text=MY_ORDERS_LOADING_TEXT,
                parse_mode="HTML",
            )

            async def open_orders_from_profile() -> None:
                try:
                    session = await container.profile_flow.get_or_create_session(
                        platform,
                        callback.from_user.id,
                    )
                    profile = await container.profile_repo.get_by_platform_user(
                        platform,
                        callback.from_user.id,
                    )
                    await open_my_orders_panel(
                        callback.message,
                        session,
                        container,
                        container.buyout_flow,
                        page=1,
                        user_id=callback.from_user.id,
                        build_reply_markup=_my_orders_reply_markup,
                        replace_message=True,
                        profile=profile,
                        loading_message=loading,
                    )
                except Exception:
                    logger.exception(
                        "Failed to open my orders from profile for user_id=%s",
                        callback.from_user.id,
                    )

            asyncio.create_task(open_orders_from_profile())
            return
        session = await container.profile_flow.get_or_create_session(platform, callback.from_user.id)
        if action == "profile:start_fill":
            profile = await container.profile_repo.get_by_platform_user(platform, callback.from_user.id)
            if profile and profile.is_filled:
                response = await container.profile_flow.show_profile_menu(session, other_platform_label="ВК")
                response.reply_markup = profile_menu_keyboard(
                    "ВК",
                    callback.from_user.id,
                    callback_codec,
                    profile=profile,
                )
                await callback.answer("Профиль уже заполнен.")
                await _apply_response(callback.message, response, edit=True)
                return
            response = await container.profile_flow.start_fill(session)
            await callback.answer()
            await _apply_response(callback.message, response, edit=True)
            return
        if action == "profile:start_sync":
            profile = await container.profile_repo.get_by_platform_user(platform, callback.from_user.id)
            if profile and profile.is_filled:
                response = await container.profile_flow.show_profile_menu(session, other_platform_label="ВК")
                response.reply_markup = profile_menu_keyboard(
                    "ВК",
                    callback.from_user.id,
                    callback_codec,
                    profile=profile,
                )
                await callback.answer("Профиль уже заполнен.")
                await _apply_response(callback.message, response, edit=True)
                return
            response = await container.profile_flow.start_sync_with_other_platform(session)
            await callback.answer()
            await _apply_response(callback.message, response, edit=True)
            return
        if action == "profile:buyout_start":
            response = await container.buyout_flow.start(session)
            await callback.answer()
            await _apply_response(callback.message, response, edit=True)
            return
        if action == "profile:track:open":
            profile = await container.profile_repo.get_by_platform_user(platform, callback.from_user.id)
            if not profile or not profile.is_filled:
                await callback.answer("Сначала заполните профиль.", show_alert=True)
                return
            response = await container.track_flow.show_mode_menu(session)
            response.reply_markup = track_mode_keyboard(callback.from_user.id, callback_codec)
            await callback.answer()
            await _apply_response(callback.message, response, edit=True)
            return
        if action in {"profile:track:mode:numbers", "profile:track:mode:comments"}:
            profile = await container.profile_repo.get_by_platform_user(platform, callback.from_user.id)
            if not profile or not profile.is_filled:
                await callback.answer("Сначала заполните профиль.", show_alert=True)
                return
            mode = "numbers" if action.endswith(":numbers") else "comments"
            response = await container.track_flow.start_mode(session, mode)
            await callback.answer()
            await _apply_response(callback.message, response, edit=True)
            return
        if action == "profile:track:more":
            response = await container.track_flow.continue_input(session)
            await callback.answer()
            await _apply_response(callback.message, response, edit=True)
            return
        if action == "profile:track:done":
            profile = await container.profile_repo.get_by_platform_user(platform, callback.from_user.id)
            if not profile or not profile.is_filled:
                await callback.answer("Сначала заполните профиль.", show_alert=True)
                return
            pending = list(session.state_data.get("track_pending") or [])
            if not pending:
                await callback.answer("Нет трек-номеров для отправки.", show_alert=True)
                return
            try:
                created = await container.track_flow.create_orders_from_tracks(profile, pending)
            except Exception:
                await callback.answer("Не удалось сохранить заказы.", show_alert=True)
                return
            if not created:
                await callback.answer("Не распознаны трек-номера для сохранения.", show_alert=True)
                return
            ok, note = await _post_user_tracks_to_group(
                callback.message,
                profile=profile,
                orders=created,
            )
            await container.track_flow.clear(session)
            await callback.answer()
            if ok:
                await callback.message.answer(f"{note} Заказы добавлены в «Мои заказы».")
            else:
                await callback.message.answer(
                    f"Заказы сохранены в «Мои заказы», но {note.lower()}"
                )
            return
        response = await container.profile_flow.handle_callback(session, action, callback_codec)
        if action in {"passport_yes", "passport_no"}:
            response.reply_markup = platforms_keyboard(callback.from_user.id, callback_codec)
        await callback.answer()
        await _apply_response(callback.message, response, edit=True)

    @router.message(_not_buyout_media_filter, F.photo | F.video | F.animation | F.document)
    async def profile_idle_media_flow(message: Message) -> None:
        if not message.from_user:
            raise SkipHandler
        if message.chat.type != "private":
            raise SkipHandler

        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return

        raise SkipHandler

    @router.message(_profile_input_state_filter, F.text)
    async def profile_text_flow(message: Message, user_session=None) -> None:
        if not message.from_user or not message.text:
            raise SkipHandler
        if message.chat.type != "private":
            raise SkipHandler
        if message.text in PROFILE_BUTTONS or message.text in CONFIRM_BUTTONS or message.text in SYNC_BUTTONS:
            raise SkipHandler
        if message.text in DELEGATED_MENU_TEXTS:
            raise SkipHandler
        if message.text.startswith("/"):
            raise SkipHandler

        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return

        session = await resolve_user_session(
            handler_data(user_session),
            container,
            platform,
            message.from_user.id,
        )
        if session is None:
            raise SkipHandler
        if session.state in {DialogState.TRACK_WAIT_INPUT, DialogState.TRACK_WAIT_CONTINUE}:
            response = await container.track_flow.handle_text(session, message.text)
            response.reply_markup = _track_reply_markup(message.from_user.id, response.state)
            await _apply_response(message, response)
            return
        if await container.admin_service.is_admin(message.from_user.id):
            if admin_session_has_pending(session):
                raise SkipHandler
        user_key = f"tg:{message.from_user.id}"
        if not container.rate_limiter.allow_request(user_key, message.text):
            return
        if not container.rate_limiter.validate_user_payload_size(len(message.text)):
            await message.answer("Сообщение слишком длинное.")
            return

        response = await container.profile_flow.handle_text(session, message.text, callback_codec=callback_codec)
        await _apply_response(message, response)

    return router
