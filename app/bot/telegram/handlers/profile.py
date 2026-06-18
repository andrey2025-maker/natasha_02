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
from app.services.admin_tools_service import GroupTopicsStore, NotificationSettingsStore, TopicDialogStore
from app.services.flows.profile_flow import FlowResponse


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

    async def _apply_response(message: Message, response: FlowResponse) -> None:
        await _dispatch_outbound(message, response)
        kwargs = {"parse_mode": "HTML"}
        if response.reply_markup is not None:
            kwargs["reply_markup"] = response.reply_markup
        await message.answer(response.text, **kwargs)

    async def _dispatch_outbound(message: Message, response: FlowResponse) -> None:
        for outgoing in response.outbound_messages:
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
        if await container.admin_service.is_admin(message.from_user.id):
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
        await _forward_idle_message_to_questions_topic(
            message=message,
            container=container,
            group_topics_store=group_topics_store,
            notification_settings_store=notification_settings_store,
            topic_dialog_store=topic_dialog_store,
            send_ack=session.state == DialogState.IDLE,
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
        user_key = f"tg:{message.from_user.id}"
        if not container.rate_limiter.allow_request(user_key, message.text):
            return
        if not container.rate_limiter.validate_user_payload_size(len(message.text)):
            await message.answer("Сообщение слишком длинное.")
            return
        if not await container.admin_service.is_admin(message.from_user.id):
            await _forward_idle_message_to_questions_topic(
                message=message,
                container=container,
                group_topics_store=group_topics_store,
                notification_settings_store=notification_settings_store,
                topic_dialog_store=topic_dialog_store,
                send_ack=session.state == DialogState.IDLE,
            )
        if session.state == DialogState.IDLE:
            return

        response = await container.profile_flow.handle_text(session, message.text, callback_codec=callback_codec)
        await _apply_response(message, response)

    return router


async def _forward_idle_message_to_questions_topic(
    message: Message,
    container: AppContainer,
    group_topics_store: GroupTopicsStore,
    notification_settings_store: NotificationSettingsStore,
    topic_dialog_store: TopicDialogStore,
    send_ack: bool = True,
) -> None:
    if not message.from_user:
        return
    profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, message.from_user.id)
    target_chat_id, default_topic_id = await group_topics_store.get_tg_topic("questions")
    if not target_chat_id:
        if send_ack:
            await message.answer("Передал вопрос менеджеру. Ответим в этом чате как можно скорее.")
        return
    topic_id = await _resolve_or_create_user_topic(
        message=message,
        target_chat_id=int(target_chat_id),
        default_topic_id=default_topic_id,
        topic_dialog_store=topic_dialog_store,
        profile=profile,
    )

    profile_hint = "без профиля"
    if profile:
        profile_hint = f"{profile.code} / {profile.name or 'без имени'}"
    body = (message.text or message.caption or "").strip()
    text = "📩 <b>Вопрос от клиента</b>\n" f"Профиль: <b>{profile_hint}</b>\n" f"TG ID: <code>{message.from_user.id}</code>\n\n"
    if body:
        text += body
    else:
        text += "Медиа-сообщение клиента ниже."
    try:
        disable_notification = await notification_settings_store.should_disable_notification("user")
        header = await message.bot.send_message(
            chat_id=int(target_chat_id),
            text=text,
            parse_mode="HTML",
            message_thread_id=topic_id,
            disable_notification=disable_notification,
        )
        await topic_dialog_store.bind_topic_message_to_user(
            chat_id=int(target_chat_id),
            topic_id=topic_id,
            topic_message_id=int(header.message_id),
            platform=Platform.TELEGRAM.value,
            platform_user_id=message.from_user.id,
        )
        if not message.text:
            copied = await message.bot.copy_message(
                chat_id=int(target_chat_id),
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=topic_id,
                reply_to_message_id=header.message_id,
                disable_notification=True,
            )
            await topic_dialog_store.bind_topic_message_to_user(
                chat_id=int(target_chat_id),
                topic_id=topic_id,
                topic_message_id=int(copied.message_id),
                platform=Platform.TELEGRAM.value,
                platform_user_id=message.from_user.id,
            )
    except Exception:
        return
    if send_ack:
        await message.answer("Передал вопрос менеджеру. Ответим в этом чате как можно скорее.")


async def _resolve_or_create_user_topic(
    message: Message,
    target_chat_id: int,
    default_topic_id: int | None,
    topic_dialog_store: TopicDialogStore,
    profile,
) -> int | None:
    if default_topic_id is None:
        return None
    existing = await topic_dialog_store.get_user_topic(
        chat_id=target_chat_id,
        platform=Platform.TELEGRAM.value,
        platform_user_id=message.from_user.id if message.from_user else 0,
    )
    if existing:
        return existing
    profile_code = profile.code if profile else "—"
    profile_name = (profile.name if profile else "") or "без имени"
    topic_name = f"{profile_code} | {profile_name} | tg:{message.from_user.id if message.from_user else 0}"
    topic_name = topic_name[:120]
    try:
        created = await message.bot.create_forum_topic(chat_id=target_chat_id, name=topic_name)
    except Exception:
        return int(default_topic_id)
    topic_id = int(created.message_thread_id)
    if message.from_user:
        await topic_dialog_store.set_user_topic(
            chat_id=target_chat_id,
            platform=Platform.TELEGRAM.value,
            platform_user_id=message.from_user.id,
            topic_id=topic_id,
        )
    return topic_id
