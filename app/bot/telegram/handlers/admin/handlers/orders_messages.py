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

def register_orders_messages(router: Router, ctx: AdminContext) -> None:
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

    @router.message(F.text.regexp(r"^order\s+status\s+\S+\s+\S+(\s*\|\s*.*)?$"))
    async def admin_order_status(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user or not message.text:
            return
        body = message.text[len("order status ") :].strip()
        note = ""
        if "|" in body:
            left, note = body.split("|", maxsplit=1)
            body = left.strip()
            note = note.strip()
        parts = body.split()
        if len(parts) < 2:
            await message.answer("Формат: order status &lt;номер&gt; &lt;статус&gt; | &lt;комментарий&gt;")
            return
        order_number, status_raw = parts[0], parts[1]
        status = _parse_order_status(status_raw)
        if status is None:
            await message.answer(
                "Неизвестный статус. Пример: order status 001/1P paid | подтверждено"
            )
            return
        updated = await container.order_admin_service.set_status(
            order_number=order_number,
            new_status=status,
            changed_by_user_id=message.from_user.id,
            note=note,
            platform=Platform.TELEGRAM,
        )
        if not updated:
            await message.answer("Заказ не найден.")
            return
        await _notify_order_status_change(
            trigger_message=message,
            container=container,
            payment_store=payment_store,
            codec=callback_codec,
            order=updated,
            new_status=status,
            note=note,
        )
        await message.answer(
            f"Статус обновлен: {updated.order_number} → {_order_status_name(updated.status)}"
        )
