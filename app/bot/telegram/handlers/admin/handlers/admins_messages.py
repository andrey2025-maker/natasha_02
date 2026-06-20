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

def register_admins_messages(router: Router, ctx: AdminContext) -> None:
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

    async def _ensure_admin(message: Message) -> bool:
        return await ctx.ensure_admin(message)

    @router.message(F.text == "Список админов")
    async def admin_list(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        is_main = message.from_user.id == container.settings.telegram.main_admin_id
        open_for_all = await admin_access_store.is_open_for_all_admins()
        if not is_main and not open_for_all:
            await message.answer("Раздел доступен только главному админу.")
            return
        admin_ids = await container.admin_service.list_admins()
        lines = [f"- {admin_id}" for admin_id in admin_ids]
        await message.answer(
            "Админы:\n" + "\n".join(lines),
            reply_markup=_admins_access_keyboard(
                user_id=message.from_user.id,
                codec=callback_codec,
                open_for_all=open_for_all,
                is_main=is_main,
                admin_ids=admin_ids,
                main_admin_id=container.settings.telegram.main_admin_id,
            ),
        )

    @router.message(F.text == "Добавить админа")
    async def add_admin_help(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer("Напишите: add_admin &lt;telegram_id&gt;")

    @router.message(F.text == "Удалить админа")
    async def remove_admin_help(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer("Напишите: del_admin &lt;telegram_id&gt;")

    @router.message(F.text.regexp(r"^(add_admin|del_admin)\s+\d+$"))
    async def admin_manage(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user or not message.text:
            return
        command, user_id_raw = message.text.split()
        target_id = int(user_id_raw)
        if command == "add_admin":
            ok = await container.admin_service.add_admin(message.from_user.id, target_id)
            await message.answer("Админ добавлен." if ok else "Только главный админ может добавлять.")
            if ok:
                try:
                    await message.bot.send_message(
                        chat_id=target_id,
                        text="Вам выданы права администратора. Кнопка «Админ» доступна в меню.",
                    )
                except Exception:
                    pass
            return
        ok = await container.admin_service.remove_admin(message.from_user.id, target_id)
        await message.answer("Админ удален." if ok else "Только главный админ может удалять.")
        if ok:
            try:
                await message.bot.send_message(
                    chat_id=target_id,
                    text="Права администратора сняты. Хороших заказов!",
                )
            except Exception:
                pass

    @router.message(F.text.regexp(r"^код\s+\d+$"))
    async def admin_profile_by_code(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.text:
            return
        code = message.text.split(maxsplit=1)[1].zfill(3)
        profile = await container.admin_service.get_profile(code)
        if not profile:
            await message.answer("Профиль не найден.")
            return
        block_reason = await block_reason_store.get_reason(profile.code)
        profile_comment = await profile_comment_store.get_comment(profile.code)
        await message.answer(
            _profile_details(profile, block_reason=block_reason, profile_comment=profile_comment),
            parse_mode="HTML",
            reply_markup=_profile_actions_keyboard(profile, message.from_user.id, callback_codec),
        )
