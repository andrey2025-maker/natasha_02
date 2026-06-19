from __future__ import annotations

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, Message

from app.bot.telegram.callbacks import CallbackAuthError, CallbackCodec
from app.bot.telegram.keyboards.main_menu import my_orders_filters_keyboard, my_orders_pagination_keyboard
from app.bot.telegram.keyboards.profile import platforms_keyboard, profile_menu_keyboard
from app.bot.texts import messages as msg
from app.core.container import AppContainer
from app.domain.enums import DialogState, Platform
from app.domain.models import OutboundMessage
from app.services.admin_tools_service import GroupTopicsStore, NotificationSettingsStore, QuestionsAlertStore, TopicDialogStore
from app.services.flows.profile_flow import FlowResponse
from app.bot.telegram.handlers.admin import admin_session_has_pending, clear_admin_input_states
from app.bot.telegram.handlers.questions_topic import (
    forward_idle_message_to_questions_topic,
    forward_message_to_dialog_topic,
)
from app.services.dialog_topic_profile_sync import refresh_dialog_topic_profile


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
}


def build_profile_router(container: AppContainer) -> Router:
    router = Router()
    platform = Platform.TELEGRAM
    callback_codec = CallbackCodec(container.callback_signer)
    group_topics_store = GroupTopicsStore(container.settings.database.dsn)
    notification_settings_store = NotificationSettingsStore(container.settings.database.dsn)
    topic_dialog_store = TopicDialogStore(container.settings.database.dsn)
    questions_alert_store = QuestionsAlertStore(container.settings.database.dsn)

    async def _apply_response(message: Message, response: object) -> None:
        outbound_messages = getattr(response, "outbound_messages", None)
        if isinstance(outbound_messages, list) and outbound_messages:
            await _dispatch_outbound(message, outbound_messages)

        text = getattr(response, "text", None)
        if not isinstance(text, str):
            profile = getattr(response, "profile", None)
            if profile is not None and getattr(profile, "telegram_user_id", None):
                await refresh_dialog_topic_profile(
                    message.bot,
                    container=container,
                    tg_user_id=int(profile.telegram_user_id),
                    group_topics_store=group_topics_store,
                    topic_dialog_store=topic_dialog_store,
                    notification_settings_store=notification_settings_store,
                )
            return
        kwargs = {"parse_mode": "HTML"}
        reply_markup = getattr(response, "reply_markup", None)
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        await message.answer(text, **kwargs)
        profile = getattr(response, "profile", None)
        if profile is not None and getattr(profile, "telegram_user_id", None):
            await refresh_dialog_topic_profile(
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
                        await refresh_dialog_topic_profile(
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
        profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, user_id)
        return bool(profile and profile.is_blocked_by_admin)

    @router.message(F.text.in_({"Профиль", "👤 Профиль"}))
    async def profile_menu(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        if await container.admin_service.is_admin(message.from_user.id):
            await clear_admin_input_states(container, session)
        response = await container.profile_flow.show_profile_menu(session, other_platform_label="ВК")
        response.reply_markup = profile_menu_keyboard("ВК", message.from_user.id, callback_codec)
        await _apply_response(message, response)

    @router.message(F.text == "Заполнить профиль")
    async def start_fill(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
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
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        response = await container.profile_flow.start_sync_with_other_platform(session)
        await _apply_response(message, response)

    @router.callback_query()
    async def profile_callbacks(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.data or not callback.message:
            return
        if await _is_blocked_user(callback.from_user.id):
            await callback.answer("Доступ ограничен", show_alert=True)
            return
        try:
            action = callback_codec.decode(callback.data, callback.from_user.id)
        except CallbackAuthError:
            return
        if action not in PROFILE_CALLBACK_ACTIONS:
            raise SkipHandler
        session = await container.profile_flow.get_or_create_session(platform, callback.from_user.id)
        if action == "profile:start_fill":
            response = await container.profile_flow.start_fill(session)
            await callback.answer()
            await _apply_response(callback.message, response)
            return
        if action == "profile:start_sync":
            response = await container.profile_flow.start_sync_with_other_platform(session)
            await callback.answer()
            await _apply_response(callback.message, response)
            return
        if action == "profile:buyout_start":
            response = await container.buyout_flow.start(session)
            await callback.answer()
            await _apply_response(callback.message, response)
            return
        if action == "profile:buyout_orders":
            response = await container.buyout_flow.render_orders(session, page=1)
            if response.state_data:
                response.reply_markup = my_orders_pagination_keyboard(
                    user_id=callback.from_user.id,
                    current_page=int(response.state_data.get("page", 1)),
                    total_pages=int(response.state_data.get("total_pages", 1)),
                    codec=callback_codec,
                )
            await callback.answer()
            await _apply_response(callback.message, response)
            return
        if action == "profile:buyout_filters":
            await container.buyout_flow.prepare_preferences(session)
            filters = container.buyout_flow.filter_states(session)
            await callback.answer()
            await callback.message.answer(
                container.buyout_flow.filters_hint_text(session),
                parse_mode="HTML",
                reply_markup=my_orders_filters_keyboard(
                    user_id=callback.from_user.id,
                    filters=filters,
                    codec=callback_codec,
                ),
            )
            return
        response = await container.profile_flow.handle_callback(session, action, callback_codec)
        if action in {"passport_yes", "passport_no"}:
            response.reply_markup = platforms_keyboard(callback.from_user.id, callback_codec)
        await callback.answer()
        await _apply_response(callback.message, response)

    @router.message(F.photo | F.video | F.animation | F.document)
    async def profile_idle_media_flow(message: Message) -> None:
        if not message.from_user:
            return
        if message.chat.type != "private":
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        is_admin = await container.admin_service.is_admin(message.from_user.id)
        if is_admin:
            if session.state == DialogState.IDLE:
                await forward_message_to_dialog_topic(
                    message=message,
                    container=container,
                    group_topics_store=group_topics_store,
                    notification_settings_store=notification_settings_store,
                    topic_dialog_store=topic_dialog_store,
                    is_admin=True,
                )
            return
        user_key = f"tg:{message.from_user.id}"
        if not container.rate_limiter.allow_request(user_key, "<media>"):
            return
        media_size_bytes = (
            (message.photo[-1].file_size if message.photo else None)
            or (message.video.file_size if message.video else None)
            or (message.animation.file_size if message.animation else None)
            or (message.document.file_size if message.document else None)
        )
        media_size_mb = int(media_size_bytes / (1024 * 1024)) if media_size_bytes else None
        if not container.rate_limiter.validate_user_payload_size(text_size=0, media_size_mb=media_size_mb):
            await message.answer("Файл слишком большой. Максимум 20 МБ.")
            return
        if session.state != DialogState.IDLE:
            return
        await forward_idle_message_to_questions_topic(
            message=message,
            container=container,
            group_topics_store=group_topics_store,
            notification_settings_store=notification_settings_store,
            topic_dialog_store=topic_dialog_store,
            questions_alert_store=questions_alert_store,
            callback_codec=callback_codec,
            send_ack=True,
        )

    @router.message()
    async def profile_text_flow(message: Message) -> None:
        if not message.from_user or not message.text:
            return
        if message.chat.type != "private":
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        if message.text in PROFILE_BUTTONS or message.text in CONFIRM_BUTTONS or message.text in SYNC_BUTTONS:
            return
        if message.text.startswith("/"):
            return

        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        if await container.admin_service.is_admin(message.from_user.id):
            if admin_session_has_pending(session):
                raise SkipHandler
        user_key = f"tg:{message.from_user.id}"
        if not container.rate_limiter.allow_request(user_key, message.text):
            return
        if not container.rate_limiter.validate_user_payload_size(len(message.text)):
            await message.answer("Сообщение слишком длинное.")
            return
        if not await container.admin_service.is_admin(message.from_user.id):
            if session.state == DialogState.IDLE:
                await forward_idle_message_to_questions_topic(
                    message=message,
                    container=container,
                    group_topics_store=group_topics_store,
                    notification_settings_store=notification_settings_store,
                    topic_dialog_store=topic_dialog_store,
                    questions_alert_store=questions_alert_store,
                    callback_codec=callback_codec,
                    send_ack=True,
                )
        elif session.state == DialogState.IDLE:
            await forward_message_to_dialog_topic(
                message=message,
                container=container,
                group_topics_store=group_topics_store,
                notification_settings_store=notification_settings_store,
                topic_dialog_store=topic_dialog_store,
                is_admin=True,
            )
        if session.state == DialogState.IDLE:
            return

        response = await container.profile_flow.handle_text(session, message.text, callback_codec=callback_codec)
        await _apply_response(message, response)

    return router
