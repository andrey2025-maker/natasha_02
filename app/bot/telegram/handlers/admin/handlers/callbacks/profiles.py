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

def register_profiles_callbacks(router: Router, ctx: AdminContext) -> None:
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

        if action.startswith("admin:profiles:"):
            payload = action.split(":", maxsplit=2)[2]
            if payload.startswith("page:"):
                page = int(payload.split(":")[1])
                await callback.answer()
                await _send_profiles_page(
                    callback.message,
                    user_id=callback.from_user.id,
                    page=page,
                    container=container,
                    codec=callback_codec,
                    edit=True,
                )
                return
            if payload == "search_menu":
                await callback.answer()
                await edit_panel_message(callback.message, text=
                    "Выберите, по чему искать профиль:",
                    reply_markup=_profiles_search_mode_keyboard(callback.from_user.id, callback_codec),
                )
                return
            if payload.startswith("search:"):
                mode = payload.split(":", maxsplit=1)[1]
                if mode not in {"code", "name", "id", "tag"}:
                    await callback.answer("Неизвестный режим", show_alert=True)
                    return
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                utils_state = _get_admin_utils_state(session)
                utils_state["awaiting_profile_search_query"] = True
                utils_state["profile_search_mode"] = mode
                await _save_admin_utils_state(container, session, utils_state)
                mode_title = {"code": "Код", "name": "Имя", "id": "ID", "tag": "Тэг"}[mode]
                await callback.answer()
                await edit_panel_message(callback.message, text=f"Введите запрос для поиска по полю «{mode_title}».")
                return

        if action.startswith("admin:blocks:"):
            payload = action.split(":", maxsplit=2)[2]
            if payload.startswith("show_blocked"):
                page = _parse_blocks_page(payload, default=1)
                blocked = await _collect_profiles(container, predicate=lambda item: item.is_blocked_by_admin, limit=500)
                reasons = await block_reason_store.list_reasons()
                await callback.answer()
                if not blocked:
                    await edit_panel_message(callback.message, text="Заблокированных админом пока нет.")
                else:
                    text, markup = _render_blocked_page(callback.from_user.id, callback_codec, blocked, page, reasons)
                    await edit_panel_message(callback.message, text=text, reply_markup=markup)
                return
            if payload.startswith("show_unsubscribed"):
                page = _parse_blocks_page(payload, default=1)
                unsubscribed = await _collect_profiles(container, predicate=lambda item: item.blocked_bot, limit=500)
                await callback.answer()
                if not unsubscribed:
                    await edit_panel_message(callback.message, text="Отписанных (заблокировали бота) пока нет.")
                else:
                    text, markup = _render_unsubscribed_page(callback.from_user.id, callback_codec, unsubscribed, page)
                    await edit_panel_message(callback.message, text=text, reply_markup=markup)
                return
            if payload == "start_block":
                await callback.answer()
                await edit_panel_message(callback.message, text=
                    "Выберите поле для поиска профиля, которого нужно заблокировать:",
                    reply_markup=_block_search_mode_keyboard(callback.from_user.id, callback_codec),
                )
                return
            if payload == "start_unblock":
                blocked = await _collect_profiles(container, predicate=lambda item: item.is_blocked_by_admin, limit=90)
                await callback.answer()
                if not blocked:
                    await edit_panel_message(callback.message, text="Заблокированных админом пока нет.")
                    return
                await edit_panel_message(callback.message, text=
                    "Выберите профиль для разблокировки:",
                    reply_markup=_block_pick_keyboard(
                        callback.from_user.id,
                        callback_codec,
                        blocked,
                        operation="unblock",
                    ),
                )
                return
            if payload.startswith("search:"):
                mode = payload.split(":", maxsplit=1)[1]
                if mode not in {"code", "name", "id", "tag"}:
                    await callback.answer("Неизвестный режим", show_alert=True)
                    return
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                utils_state = _get_admin_utils_state(session)
                utils_state["awaiting_block_search_query"] = True
                utils_state["block_search_mode"] = mode
                utils_state["block_operation"] = "block"
                await _save_admin_utils_state(container, session, utils_state)
                mode_title = {"code": "Код", "name": "Имя", "id": "ID", "tag": "Тэг"}[mode]
                await callback.answer()
                await edit_panel_message(callback.message, text=f"Введите запрос для блокировки (поле «{mode_title}»).")
                return

        if action.startswith("admin:blockpick:"):
            _, _, operation, code = action.split(":", maxsplit=3)
            if operation not in {"block", "unblock"}:
                await callback.answer("Некорректная операция", show_alert=True)
                return
            if operation == "block":
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                utils_state = _get_admin_utils_state(session)
                _reset_admin_utils_waiters(utils_state)
                utils_state["awaiting_block_reason_for_code"] = code
                await _save_admin_utils_state(container, session, utils_state)
                await callback.answer()
                await edit_panel_message(callback.message, text=
                    f"Введите причину блокировки для кода {code}.\n"
                    "Если причина не нужна, отправьте `-`.",
                    parse_mode="Markdown",
                )
                return
            ok = await container.admin_service.set_block_status(code, blocked=False)
            if ok:
                await block_reason_store.clear_reason(code)
            await callback.answer("Пользователь разблокирован" if ok else "Профиль не найден")
            return

        if action.startswith("admin:profile:view:"):
            code = action.split(":")[-1]
            profile = await container.admin_service.get_profile(code)
            if not profile:
                await callback.answer("Профиль не найден", show_alert=True)
                return
            block_reason = await block_reason_store.get_reason(profile.code)
            profile_comment = await profile_comment_store.get_comment(profile.code)
            await callback.answer()
            await edit_panel_message(callback.message, text=
                _profile_details(profile, block_reason=block_reason, profile_comment=profile_comment),
                parse_mode="HTML",
                reply_markup=_profile_actions_keyboard(profile, callback.from_user.id, callback_codec),
            )
            return

        if action.startswith("admin:profile:edit:"):
            code = action.split(":")[-1]
            profile = await container.admin_service.get_profile(code)
            if not profile:
                await callback.answer("Профиль не найден", show_alert=True)
                return
            await callback.answer()
            await edit_panel_message(callback.message, text=
                f"Что редактировать в профиле <b>{_h(code)}</b>?",
                parse_mode="HTML",
                reply_markup=_profile_edit_fields_keyboard(code, callback.from_user.id, callback_codec),
            )
            return

        if action.startswith("admin:profile:edit_field:"):
            parts = action.split(":", maxsplit=4)
            if len(parts) != 5:
                await callback.answer("Некорректная команда", show_alert=True)
                return
            code = parts[3]
            field = parts[4]
            if field not in {"name", "phone", "city", "passport", "comment", "code"}:
                await callback.answer("Неизвестное поле", show_alert=True)
                return
            profile = await container.admin_service.get_profile(code)
            if not profile:
                await callback.answer("Профиль не найден", show_alert=True)
                return
            session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
            utils_state = _get_admin_utils_state(session)
            _reset_admin_utils_waiters(utils_state)
            utils_state["awaiting_profile_edit_code"] = code
            utils_state["awaiting_profile_edit_field"] = field
            await _save_admin_utils_state(container, session, utils_state)
            field_title = {
                "name": "Имя",
                "phone": "Телефон",
                "city": "Город",
                "passport": "Загран паспорт",
                "comment": "Комментарий",
                "code": "Код",
            }[field]
            if field == "passport":
                hint = "Введите Да/Нет."
            elif field == "code":
                hint = "Отправьте новый числовой код. Пример: 016"
            else:
                hint = "Отправьте новое значение."
            await callback.answer()
            await edit_panel_message(callback.message, text=fsm_prompt(f"Редактирование поля «{field_title}» для кода {code}.\n{hint}"))
            return

        if action.startswith("admin:profile:comment:"):
            code = action.split(":")[-1]
            profile = await container.admin_service.get_profile(code)
            if not profile:
                await callback.answer("Профиль не найден", show_alert=True)
                return
            session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
            utils_state = _get_admin_utils_state(session)
            _reset_admin_utils_waiters(utils_state)
            utils_state["awaiting_profile_comment_code"] = code
            await _save_admin_utils_state(container, session, utils_state)
            current_comment = await profile_comment_store.get_comment(code)
            await callback.answer()
            await edit_panel_message(callback.message, text=
                "Введите комментарий для профиля.\n"
                "Чтобы очистить, отправьте `-`.\n\n"
                f"Текущий: {current_comment or '—'}",
                parse_mode="Markdown",
            )
            return


        raise SkipHandler
