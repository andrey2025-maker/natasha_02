from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, Message

from app.bot.telegram.callbacks import CallbackAuthError, CallbackCodec
from app.bot.telegram.callback_panel import edit_panel_message
from app.bot.telegram.keyboards.main_menu import my_orders_message_keyboard
from app.bot.telegram.keyboards.profile import (
    platforms_keyboard,
    profile_menu_keyboard,
    track_continue_keyboard,
    track_mode_keyboard,
)
from app.bot.telegram.my_orders_media import present_my_orders_panel
from app.bot.texts import messages as msg
from app.core.container import AppContainer
from app.domain.enums import DialogState, Platform
from app.domain.models import BuyoutOrder, OutboundMessage
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
    questions_alert_store = QuestionsAlertStore(container.settings.database.dsn)

    async def _apply_response(message: Message, response: object, *, edit: bool = False) -> None:
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
        response.reply_markup = profile_menu_keyboard(
            "ВК",
            message.from_user.id,
            callback_codec,
            profile=response.profile,
        )
        await _apply_response(message, response)

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
        if await _is_blocked_user(callback.from_user.id):
            await callback.answer("Доступ ограничен", show_alert=True)
            return
        try:
            action = callback_codec.decode(callback.data, callback.from_user.id)
        except CallbackAuthError:
            raise SkipHandler
        if action not in PROFILE_CALLBACK_ACTIONS:
            raise SkipHandler
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
        if action == "profile:buyout_orders":
            await container.buyout_flow.prepare_preferences(session)
            response = await container.buyout_flow.render_orders(session, page=1)
            if response.state_data:
                filters = container.buyout_flow.filter_states(session)
                response.reply_markup = my_orders_message_keyboard(
                    user_id=callback.from_user.id,
                    current_page=int(response.state_data.get("page", 1)),
                    total_pages=int(response.state_data.get("total_pages", 1)),
                    filters=filters,
                    codec=callback_codec,
                )
            await callback.answer()
            await present_my_orders_panel(
                callback.message,
                session,
                container,
                text=response.text,
                order_media_groups=response.order_media_groups,
                reply_markup=response.reply_markup,
                replace_message=True,
            )
            return
        if action == "profile:buyout_filters":
            await container.buyout_flow.prepare_preferences(session)
            response = await container.buyout_flow.render_orders(session, page=1)
            if response.state_data:
                filters = container.buyout_flow.filter_states(session)
                response.reply_markup = my_orders_message_keyboard(
                    user_id=callback.from_user.id,
                    current_page=int(response.state_data.get("page", 1)),
                    total_pages=int(response.state_data.get("total_pages", 1)),
                    filters=filters,
                    codec=callback_codec,
                )
            await callback.answer()
            await present_my_orders_panel(
                callback.message,
                session,
                container,
                text=response.text,
                order_media_groups=response.order_media_groups,
                reply_markup=response.reply_markup,
                replace_message=True,
            )
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
                    f"Заказы сохранены в «Мои заказы», но {note[0].lower()}{note[1:]}"
                )
            return
        response = await container.profile_flow.handle_callback(session, action, callback_codec)
        if action in {"passport_yes", "passport_no"}:
            response.reply_markup = platforms_keyboard(callback.from_user.id, callback_codec)
        await callback.answer()
        await _apply_response(callback.message, response, edit=True)

    @router.message(F.photo | F.video | F.animation | F.document)
    async def profile_idle_media_flow(message: Message) -> None:
        if not message.from_user:
            raise SkipHandler
        if message.chat.type != "private":
            raise SkipHandler
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        is_admin = await container.admin_service.is_admin(message.from_user.id)
        if is_admin:
            if session.state == DialogState.IDLE:
                logs_chat_id, _ = await group_topics_store.get_tg_topic("logs")
                if not logs_chat_id:
                    await message.answer(
                        "Диалоговая группа не настроена. "
                        "Задайте chat_id в Админ → Утилиты → Группа."
                    )
                    return
                copied = await forward_message_to_dialog_topic(
                    message=message,
                    container=container,
                    group_topics_store=group_topics_store,
                    notification_settings_store=notification_settings_store,
                    topic_dialog_store=topic_dialog_store,
                )
                if not copied:
                    await message.answer(
                        "Не удалось создать тему или доставить сообщение в группу. "
                        "Проверьте, что группа — форум и у бота есть права на управление темами."
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
            raise SkipHandler
        if message.chat.type != "private":
            raise SkipHandler
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        if message.text in PROFILE_BUTTONS or message.text in CONFIRM_BUTTONS or message.text in SYNC_BUTTONS:
            return
        if message.text.startswith("/"):
            return

        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        if session.state in {
            DialogState.BUYOUT_WAIT_MEDIA,
            DialogState.BUYOUT_WAIT_LINK,
            DialogState.BUYOUT_WAIT_DETAILS,
            DialogState.BUYOUT_ADD_MORE,
        }:
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
            logs_chat_id, _ = await group_topics_store.get_tg_topic("logs")
            if not logs_chat_id:
                await message.answer(
                    "Диалоговая группа не настроена. "
                    "Задайте chat_id в Админ → Утилиты → Группа."
                )
                return
            copied = await forward_message_to_dialog_topic(
                message=message,
                container=container,
                group_topics_store=group_topics_store,
                notification_settings_store=notification_settings_store,
                topic_dialog_store=topic_dialog_store,
            )
            if not copied:
                await message.answer(
                    "Не удалось создать тему или доставить сообщение в группу. "
                    "Проверьте, что группа — форум и у бота есть права на управление темами."
                )
        if session.state == DialogState.IDLE:
            return

        response = await container.profile_flow.handle_text(session, message.text, callback_codec=callback_codec)
        await _apply_response(message, response)

    return router
