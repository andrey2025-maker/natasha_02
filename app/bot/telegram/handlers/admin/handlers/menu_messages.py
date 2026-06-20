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
from app.bot.telegram.handlers.admin.panel import send_admin_panel
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

def register_menu_messages(router: Router, ctx: AdminContext) -> None:
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

    @router.message(F.text.in_({"Админ", "🛠 Админ", "🛠️ Админ"}))
    async def admin_root(message: Message) -> None:
        if not await _ensure_admin(message):
            await message.answer("⛔ Доступ к админ-панели только у администраторов.")
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        await _clear_admin_input_states(container, session)
        await send_admin_panel(
            message,
            container=container,
            user_id=message.from_user.id,
            callback_codec=callback_codec,
        )

    @router.message(F.text == "Назад")
    async def admin_back(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer("Главное меню", reply_markup=main_menu_keyboard(include_admin=True))

    @router.message(F.text == "Профили")
    async def admin_profiles(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        utils_state = _get_admin_utils_state(session)
        utils_state["awaiting_profile_search_query"] = False
        utils_state["profile_search_mode"] = None
        await _save_admin_utils_state(container, session, utils_state)
        await _send_profiles_page(
            message,
            user_id=message.from_user.id,
            page=1,
            container=container,
            codec=callback_codec,
        )

    @router.message(F.text == "Заказы")
    async def admin_orders(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer(
            "📦 Раздел заказов.\n"
            "Используйте команды: «Выкупы» или «Самовыкуп».",
        )

    @router.message(F.text == "Выкупы")
    async def admin_orders_buyout(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_orders_state(session)
        state["page"] = 1
        await _save_admin_orders_state(container, session, state)
        await _send_orders_panel(message, container, callback_codec, message.from_user.id, state)

    @router.message(F.text == "Самовыкуп")
    async def admin_orders_self_buyout(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer(
            "Пока не сделано, Мать Китайчат не объяснила всю суть PRO-CARGO и 1999CARGO! "
            "А так понимание как сюда засунуть пикалку заказов есть, нужно лишь больше информации "
            "для @andreyhggh о взаимодействия с платформами PRO-CARGO и 1999CARGO!"
        )

    @router.message(F.text == "Статистика")
    async def admin_stats(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        text = await container.stats_service.build_overview_text()
        await message.answer(text, parse_mode="HTML")

    @router.message(F.text == "Рассылка")
    async def admin_broadcast(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer(
            "Выберите аудиторию для рассылки, затем отправьте текст или одно медиа с подписью.",
            reply_markup=_broadcast_keyboard(message.from_user.id, callback_codec),
        )

    @router.message(F.text == "Утилиты")
    async def admin_utils(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer(
            "🧰 Утилиты админки.\n"
            "Разделы: «Бэкапы», «Коды», «Группа», «Оплата», «Оплаты группа».",
        )
