from __future__ import annotations

import re

import aiohttp
from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from app.bot.telegram.callbacks import CallbackAuthError, CallbackCodec
from app.bot.telegram.callback_panel import edit_panel_message
from app.bot.telegram.fsm_utils import (
    admin_utils_has_waiter,
    fsm_prompt,
    is_cancel_command,
    is_navigation_command,
)
from app.bot.telegram.handlers.admin.all_helpers import *
from app.bot.telegram.handlers.admin.context import AdminContext
from app.bot.telegram.handlers.content_utils_admin import (
    SCREEN_EDIT_MEDIA as CONTENT_UTILS_EDIT_MEDIA,
    SCREEN_EDIT_MENU as CONTENT_UTILS_EDIT_MENU,
    handle_content_utils_callback,
    refresh_content_utils_panel,
    reset_content_utils_state,
    try_handle_content_utils_text,
)
from app.bot.telegram.handlers.faq_admin import (
    SCREEN_CONTENT,
    SCREEN_EDIT_MEDIA,
    handle_faq_admin_callback,
    open_faq_admin_panel,
    refresh_faq_admin_panel,
    reset_faq_admin_state,
    try_handle_faq_admin_text,
)
from app.bot.telegram.handlers.questions_topic import ensure_dialog_topic_for_telegram_user
from app.bot.telegram.keyboards.profile import main_menu_keyboard
from app.core.container import AppContainer
from app.domain.enums import DialogState, OrderStatus, Platform
from app.domain.models import OutboundMessage, UserProfile
from app.services.admin_tools_service import (
    count_targets_for_platform,
    parse_codes,
    send_stored_media_to_telegram,
)
from app.services.dialog_topic_profile_sync import refresh_dialog_topic_profile

def register_broadcast_callbacks(router: Router, ctx: AdminContext) -> None:
    container = ctx.container
    callback_codec = ctx.callback_codec
    payment_store = ctx.payment_store
    payment_target_store = ctx.payment_target_store
    notification_settings_store = ctx.notification_settings_store
    prohibited_store = ctx.prohibited_store
    admin_access_store = ctx.admin_access_store
    block_reason_store = ctx.block_reason_store
    profile_comment_store = ctx.profile_comment_store
    faq_media_store = ctx.faq_media_store
    group_topics_store = ctx.group_topics_store
    topic_dialog_store = ctx.topic_dialog_store
    delivery_store = ctx.delivery_store
    contacts_store = ctx.contacts_store
    backup_service = ctx.backup_service

    @router.callback_query()
    async def admin_callbacks(callback: CallbackQuery) -> None:
        if not callback.data or not callback.from_user or not callback.message:
            raise SkipHandler
        if not await container.admin_service.is_admin(callback.from_user.id):
            raise SkipHandler
        try:
            action = callback_codec.decode(callback.data, callback.from_user.id)
        except CallbackAuthError:
            raise SkipHandler

        if action.startswith("admin:broadcast:"):
            audience = action.split(":")[-1]
            if audience not in {"all", "active", "inactive", "codes"}:
                await callback.answer("Неизвестная аудитория", show_alert=True)
                return
            session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
            state = _get_admin_broadcast_state(session)
            if audience == "codes":
                state["awaiting_codes"] = True
                state["awaiting_payload"] = False
                state["audience"] = "codes"
                state["target_codes"] = []
                await _save_admin_broadcast_state(container, session, state)
                await callback.answer()
                await edit_panel_message(
                    callback.message,
                    text=fsm_prompt(
                        "Перечислите коды клиентов — по одному на строку или через запятую.\n"
                        "Пример:\n"
                        "001\n"
                        "002\n"
                        "016"
                    ),
                )
                return

            state["awaiting_codes"] = False
            state["awaiting_payload"] = True
            state["audience"] = audience
            state["target_codes"] = []
            await _save_admin_broadcast_state(container, session, state)
            profiles = await backup_service.pick_profiles_for_broadcast(audience)
            tg_count = count_targets_for_platform(profiles, Platform.TELEGRAM)
            vk_count = count_targets_for_platform(profiles, Platform.VK)
            await callback.answer()
            await edit_panel_message(callback.message, text=
                fsm_prompt(
                    "Аудитория выбрана.\n"
                    f"Получатели: TG {tg_count}, VK {vk_count}\n"
                    "Теперь отправьте текст рассылки одним сообщением."
                )
            )
            return

        if action.startswith("admin:backup:auto:"):
            mode = action.split(":")[-1]
            enabled = mode == "on"
            await backup_service.set_auto_backup_enabled(enabled)
            await callback.answer("Авто-бэкап обновлен")
            await callback.message.edit_reply_markup(
                reply_markup=_backup_keyboard_with_back(callback.from_user.id, callback_codec, enabled)
            )
            return

        if action.startswith("admin:notify:toggle:"):
            key = action.split(":")[-1]
            settings = await notification_settings_store.toggle(key)
            await callback.answer("Настройки обновлены")
            await callback.message.edit_text(
                _notification_settings_text(settings),
                reply_markup=_notifications_keyboard_with_back(callback.from_user.id, callback_codec, settings),
            )
            return

        if action == "admin:admins:toggle_access":
            if callback.from_user.id != container.settings.telegram.main_admin_id:
                await callback.answer("Только главный админ", show_alert=True)
                return
            new_value = await admin_access_store.toggle()
            admin_ids = await container.admin_service.list_admins()
            lines = [f"- {admin_id}" for admin_id in admin_ids]
            await callback.answer("Доступ обновлен")
            await callback.message.edit_text(
                "Админы:\n" + "\n".join(lines),
                reply_markup=_admins_access_keyboard_with_back(
                    user_id=callback.from_user.id,
                    codec=callback_codec,
                    open_for_all=new_value,
                    is_main=True,
                    admin_ids=admin_ids,
                    main_admin_id=container.settings.telegram.main_admin_id,
                ),
            )
            return

        if action == "admin:admins:add_id":
            if callback.from_user.id != container.settings.telegram.main_admin_id:
                await callback.answer("Только главный админ", show_alert=True)
                return
            session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
            utils_state = _get_admin_utils_state(session)
            _reset_admin_utils_waiters(utils_state)
            utils_state["awaiting_admin_add_id"] = True
            await _save_admin_utils_state(container, session, utils_state)
            await callback.answer()
            await edit_panel_message(callback.message, text="Введите Telegram ID пользователя для добавления в админы.")
            return

        if action == "admin:admins:add_code":
            if callback.from_user.id != container.settings.telegram.main_admin_id:
                await callback.answer("Только главный админ", show_alert=True)
                return
            session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
            utils_state = _get_admin_utils_state(session)
            _reset_admin_utils_waiters(utils_state)
            utils_state["awaiting_admin_add_code"] = True
            await _save_admin_utils_state(container, session, utils_state)
            await callback.answer()
            await edit_panel_message(callback.message, text="Введите код профиля (например, 001) для добавления в админы.")
            return

        if action.startswith("admin:admins:remove:"):
            if callback.from_user.id != container.settings.telegram.main_admin_id:
                await callback.answer("Только главный админ", show_alert=True)
                return
            target_id = int(action.split(":")[-1])
            ok = await container.admin_service.remove_admin(callback.from_user.id, target_id)
            if ok:
                try:
                    await callback.bot.send_message(
                        chat_id=target_id,
                        text="Права администратора сняты. Хороших заказов!",
                    )
                except Exception:
                    pass
            admin_ids = await container.admin_service.list_admins()
            lines = [f"- {admin_id}" for admin_id in admin_ids]
            await callback.answer("Админ удален" if ok else "Не удалось удалить", show_alert=not ok)
            await callback.message.edit_text(
                "Админы:\n" + "\n".join(lines),
                reply_markup=_admins_access_keyboard_with_back(
                    user_id=callback.from_user.id,
                    codec=callback_codec,
                    open_for_all=await admin_access_store.is_open_for_all_admins(),
                    is_main=True,
                    admin_ids=admin_ids,
                    main_admin_id=container.settings.telegram.main_admin_id,
                ),
            )
            return

        if action == "admin:backup:db":
            await callback.answer("Готовлю SQL-бэкап...")
            file_path = await backup_service.create_db_backup()
            disable_notification = await notification_settings_store.should_disable_notification("bot")
            await callback.message.answer_document(
                FSInputFile(str(file_path)),
                caption=f"Бэкап БД: {file_path.name}",
                disable_notification=disable_notification,
            )
            return

        if action == "admin:backup:excel":
            await callback.answer("Готовлю CSV (Excel) ...")
            file_path = await backup_service.create_excel_backup()
            disable_notification = await notification_settings_store.should_disable_notification("bot")
            await callback.message.answer_document(
                FSInputFile(str(file_path)),
                caption=f"Excel-бэкап: {file_path.name}",
                disable_notification=disable_notification,
            )
            return


        raise SkipHandler
