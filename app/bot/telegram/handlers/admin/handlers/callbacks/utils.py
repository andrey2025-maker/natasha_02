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

def register_utils_callbacks(router: Router, ctx: AdminContext) -> None:
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
            return
        if not await container.admin_service.is_admin(callback.from_user.id):
            raise SkipHandler
        try:
            action = callback_codec.decode(callback.data, callback.from_user.id)
        except CallbackAuthError:
            raise SkipHandler

        if action.startswith("admin:utils:"):
            utils_action = action.split(":", maxsplit=2)[2]
            if (
                utils_action == "prohibited"
                or utils_action == "contacts"
                or utils_action.startswith("prohibited:")
                or utils_action.startswith("contacts:")
            ):
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                state = _get_admin_utils_state(session)
                handled = await handle_content_utils_callback(
                    callback,
                    utils_action=utils_action,
                    codec=callback_codec,
                    utils_state=state,
                    prohibited_store=prohibited_store,
                    contacts_store=contacts_store,
                )
                if handled:
                    await _save_admin_utils_state(container, session, state)
                    return
            if utils_action == "root":
                await callback.answer()
                await callback.message.answer(
                    "🧰 Утилиты админки.\nВыберите подраздел:",
                    reply_markup=_utils_inline_keyboard(callback.from_user.id, callback_codec),
                )
                return
            if utils_action == "group":
                target_chat_id, target_topic_id = await group_topics_store.get_tg_topic("logs")
                _, payment_topic_id = await group_topics_store.get_tg_topic("payment")
                _, questions_topic_id = await group_topics_store.get_tg_topic("questions")
                _, buyout_topic_id = await group_topics_store.get_tg_topic("buyout")
                vk_logs_peer_id = await group_topics_store.get_vk_logs_peer_id()
                await callback.answer()
                await callback.message.answer(
                    "🛰 Группа (темы диалогов):\n"
                    f"chat_id={target_chat_id or 'не задан'}, logs={target_topic_id or '—'}, "
                    f"payment={payment_topic_id or '—'}, questions={questions_topic_id or '—'}, "
                    f"buyout={buyout_topic_id or '—'}\n"
                    f"VK logs peer_id={vk_logs_peer_id or 'не задан'}",
                    reply_markup=_utils_group_keyboard(callback.from_user.id, callback_codec),
                )
                return
            if utils_action == "group:add":
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                state = _get_admin_utils_state(session)
                _reset_admin_utils_waiters(state)
                state["awaiting_backup_target"] = True
                await _save_admin_utils_state(container, session, state)
                await callback.answer()
                await callback.message.answer(fsm_prompt("Отправьте chat_id группы, например: -1001234567890"))
                return
            if utils_action == "group:notifications":
                settings = await notification_settings_store.get_settings()
                await callback.answer()
                await callback.message.answer(
                    _notification_settings_text(settings),
                    reply_markup=_notifications_keyboard_with_back(callback.from_user.id, callback_codec, settings),
                )
                return
            if utils_action == "group:topics":
                selected = await group_topics_store.get_topic_name_parts()
                await callback.answer()
                await callback.message.answer(
                    "Шаблон подписи темы для новых диалогов.\n"
                    "Выбранные поля отмечены зеленым.",
                    reply_markup=_utils_topics_keyboard(callback.from_user.id, callback_codec, selected),
                )
                return
            if utils_action.startswith("group:topics:toggle:"):
                part = utils_action.split(":")[-1]
                selected = await group_topics_store.toggle_topic_name_part(part)
                await callback.answer("Обновлено")
                await callback.message.edit_reply_markup(
                    reply_markup=_utils_topics_keyboard(callback.from_user.id, callback_codec, selected)
                )
                return
            if utils_action == "ref":
                await callback.answer()
                await callback.message.answer(
                    "Хозяйка Наталья🐢, Повелительница Китайчиков я бы с радостью награждал друзей, "
                    "но ты пока не придумала условия реферальной системы. "
                    "Давай сделаем это вместе - я подскажу, если что! Напиши если созреешь @andreyhggh",
                    reply_markup=_utils_back_keyboard(callback.from_user.id, callback_codec),
                )
                return
            if utils_action == "backups":
                enabled = await backup_service.auto_backup_enabled()
                await callback.answer()
                await callback.message.answer(
                    "🗂 Бэкапы: выгрузка БД/Excel и авто-бэкап каждые 4 часа.",
                    reply_markup=_backup_keyboard_with_back(callback.from_user.id, callback_codec, enabled),
                )
                return
            if utils_action == "admins":
                is_main = callback.from_user.id == container.settings.telegram.main_admin_id
                open_for_all = await admin_access_store.is_open_for_all_admins()
                if not is_main and not open_for_all:
                    await callback.answer("Раздел доступен только главному админу.", show_alert=True)
                    return
                admin_ids = await container.admin_service.list_admins()
                lines = [f"- {admin_id}" for admin_id in admin_ids]
                await callback.answer()
                await callback.message.answer(
                    "Админы:\n" + "\n".join(lines),
                    reply_markup=_admins_access_keyboard_with_back(
                        user_id=callback.from_user.id,
                        codec=callback_codec,
                        open_for_all=open_for_all,
                        is_main=is_main,
                        admin_ids=admin_ids,
                        main_admin_id=container.settings.telegram.main_admin_id,
                    ),
                )
                return
            if utils_action == "codes":
                reserved = await container.code_reserve_repo.list_reserved()
                preview = ", ".join(reserved[:40]) if reserved else "пусто"
                if len(reserved) > 40:
                    preview += ", ..."
                await callback.answer()
                await callback.message.answer(
                    "🔐 Резерв кодов:\n"
                    f"{preview}",
                    reply_markup=_codes_inline_keyboard(callback.from_user.id, callback_codec),
                )
                return
            if utils_action == "codes:add":
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                state = _get_admin_utils_state(session)
                _reset_admin_utils_waiters(state)
                state["awaiting_codes_add"] = True
                await _save_admin_utils_state(container, session, state)
                await callback.answer()
                await callback.message.answer("Отправьте коды для добавления (через запятую или по строкам).")
                return
            if utils_action == "codes:remove":
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                state = _get_admin_utils_state(session)
                _reset_admin_utils_waiters(state)
                state["awaiting_codes_remove"] = True
                await _save_admin_utils_state(container, session, state)
                await callback.answer()
                await callback.message.answer("Отправьте коды для удаления (через запятую или по строкам).")
                return
            if utils_action == "payment":
                text = await payment_store.get_text()
                media_items = await payment_store.get_media_items()
                await callback.answer()
                await callback.message.answer(
                    "💸 Контент оплаты для пользователей:\n\n"
                    f"{text}\n\n"
                    f"Медиа: {len(media_items)}\n{_media_items_summary(media_items)}",
                    reply_markup=_payment_inline_keyboard(callback.from_user.id, callback_codec),
                )
                return
            if utils_action == "payment:edit":
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                state = _get_admin_utils_state(session)
                _reset_admin_utils_waiters(state)
                state["awaiting_payment_text"] = True
                state["awaiting_payment_media"] = True
                await _save_admin_utils_state(container, session, state)
                await callback.answer()
                await callback.message.answer(
                    "Режим редактирования оплаты.\n"
                    "1) Отправьте новый текст.\n"
                    "2) Затем отправляйте медиа.\n"
                    "Когда закончите — нажмите «Готово медиа»."
                )
                return
            if utils_action == "payment:media_done":
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                state = _get_admin_utils_state(session)
                state["awaiting_payment_media"] = False
                state["awaiting_payment_text"] = False
                await _save_admin_utils_state(container, session, state)
                await callback.answer("Сохранено")
                return
            await callback.answer()
            return


        raise SkipHandler
