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

def register_menu_callbacks(router: Router, ctx: AdminContext) -> None:
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

        if action.startswith("admin:menu:"):
            menu_action = action.split(":", maxsplit=2)[2]
            if menu_action == "profiles":
                await callback.answer()
                await _send_profiles_page(
                    callback.message,
                    user_id=callback.from_user.id,
                    page=1,
                    container=container,
                    codec=callback_codec,
                    edit=True,
                )
                return
            if menu_action == "blocks":
                await callback.answer()
                await edit_panel_message(callback.message, text=
                    "Управление блокировками:",
                    reply_markup=_blocks_menu_keyboard(callback.from_user.id, callback_codec),
                )
                return
            if menu_action == "orders":
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                state = _get_admin_orders_state(session)
                state["page"] = 1
                state["search_results"] = None
                state["awaiting_order_search_query"] = False
                state["order_search_mode"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer()
                await _send_orders_panel(
                    callback.message,
                    container,
                    callback_codec,
                    callback.from_user.id,
                    state,
                    session,
                    edit=True,
                )
                return
            if menu_action == "stats":
                text = await container.stats_service.build_overview_text()
                await callback.answer()
                await edit_panel_message(callback.message, text=text, parse_mode="HTML")
                return
            if menu_action == "faq":
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                state = _get_admin_utils_state(session)
                _reset_admin_utils_waiters(state)
                await _save_admin_utils_state(container, session, state)
                await callback.answer()
                await open_faq_admin_panel(
                    callback.message,
                    container=container,
                    codec=callback_codec,
                    user_id=callback.from_user.id,
                    utils_state=state,
                    faq_media_store=faq_media_store,
                    edit=True,
                )
                await _save_admin_utils_state(container, session, state)
                return
            if menu_action == "admins":
                is_main = callback.from_user.id == container.settings.telegram.main_admin_id
                open_for_all = await admin_access_store.is_open_for_all_admins()
                if not is_main and not open_for_all:
                    await callback.answer("Раздел доступен только главному админу.", show_alert=True)
                    return
                admin_ids = await container.admin_service.list_admins()
                lines = [f"- {admin_id}" for admin_id in admin_ids]
                await callback.answer()
                await edit_panel_message(callback.message, text=
                    "Админы:\n" + "\n".join(lines),
                    reply_markup=_admins_access_keyboard(
                        user_id=callback.from_user.id,
                        codec=callback_codec,
                        open_for_all=open_for_all,
                        is_main=is_main,
                        admin_ids=admin_ids,
                        main_admin_id=container.settings.telegram.main_admin_id,
                    ),
                )
                return
            if menu_action == "broadcast":
                await callback.answer()
                await edit_panel_message(callback.message, text=
                    "Выберите аудиторию для рассылки, затем отправьте текст или одно медиа с подписью.",
                    reply_markup=_broadcast_keyboard(callback.from_user.id, callback_codec),
                )
                return
            if menu_action == "utils":
                await callback.answer()
                await edit_panel_message(
                    callback.message,
                    text=UTILS_PANEL_TEXT,
                    reply_markup=_utils_inline_keyboard(callback.from_user.id, callback_codec),
                )
                return
            await callback.answer()
            return


        raise SkipHandler
