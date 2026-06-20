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

def register_callbacks(router: Router, ctx: AdminContext) -> None:
    from app.bot.telegram.handlers.admin.handlers.callbacks.block import register_block_callbacks
    from app.bot.telegram.handlers.admin.handlers.callbacks.broadcast import register_broadcast_callbacks
    from app.bot.telegram.handlers.admin.handlers.callbacks.faq import register_faq_callbacks
    from app.bot.telegram.handlers.admin.handlers.callbacks.menu import register_menu_callbacks
    from app.bot.telegram.handlers.admin.handlers.callbacks.orders import register_orders_callbacks
    from app.bot.telegram.handlers.admin.handlers.callbacks.profiles import register_profiles_callbacks
    from app.bot.telegram.handlers.admin.handlers.callbacks.utils import register_utils_callbacks

    sub = Router()
    register_menu_callbacks(sub, ctx)
    register_faq_callbacks(sub, ctx)
    register_utils_callbacks(sub, ctx)
    register_broadcast_callbacks(sub, ctx)
    register_profiles_callbacks(sub, ctx)
    register_orders_callbacks(sub, ctx)
    register_block_callbacks(sub, ctx)
    router.include_router(sub)
