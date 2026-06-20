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

def register_orders_callbacks(router: Router, ctx: AdminContext) -> None:
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

        if action.startswith("admin:orders:"):
            session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
            state = _get_admin_orders_state(session)
            payload = action.split(":", maxsplit=2)[2]

            if payload.startswith("page:"):
                page = max(1, int(payload.split(":")[1]))
                state["page"] = page
                await _save_admin_orders_state(container, session, state)
            elif payload.startswith("filter:"):
                status_raw = payload.split(":", maxsplit=1)[1]
                try:
                    status = OrderStatus(status_raw)
                except ValueError:
                    await callback.answer("Неизвестный фильтр", show_alert=True)
                    return
                _toggle_admin_order_status_filter(state, status)
                await _save_admin_orders_state(container, session, state)
                await callback.answer("Фильтр обновлен")
            elif payload == "search_menu":
                await callback.answer()
                await edit_panel_message(
                    callback.message,
                    text="Выберите, по чему искать заказ:",
                    reply_markup=_orders_search_mode_keyboard(callback.from_user.id, callback_codec),
                )
                return
            elif payload.startswith("search:"):
                mode = payload.split(":", maxsplit=1)[1]
                if mode not in {"order_number", "code", "track"}:
                    await callback.answer("Неизвестный режим поиска", show_alert=True)
                    return
                state["awaiting_order_search_query"] = True
                state["order_search_mode"] = mode
                await _save_admin_orders_state(container, session, state)
                prompts = {
                    "order_number": "Введите номер заказа (можно часть номера):",
                    "code": "Введите код клиента (например, 001):",
                    "track": "Введите трек-номер (можно часть):",
                }
                await callback.answer()
                await edit_panel_message(callback.message, text=prompts[mode])
                return
            elif payload.startswith("toggle:"):
                order_number = payload.split(":", maxsplit=1)[1]
                selected = set(state.get("selected", []))
                if order_number in selected:
                    selected.remove(order_number)
                else:
                    selected.add(order_number)
                state["selected"] = sorted(selected)
                await _save_admin_orders_state(container, session, state)
            elif payload.startswith("set_status:"):
                status_raw = payload.split(":", maxsplit=1)[1]
                status = _parse_order_status(status_raw)
                if status is None:
                    await callback.answer("Неизвестный статус", show_alert=True)
                    return
                selected = list(state.get("selected", []))
                if not selected:
                    await callback.answer("Выберите хотя бы один заказ", show_alert=True)
                    return
                changed = 0
                for order_number in selected:
                    updated = await container.order_admin_service.set_status(
                        order_number=order_number,
                        new_status=status,
                        changed_by_user_id=callback.from_user.id,
                        note="bulk update",
                        platform=Platform.TELEGRAM,
                    )
                    if updated:
                        changed += 1
                        await _notify_order_status_change(
                            trigger_message=callback.message,
                            container=container,
                            payment_store=payment_store,
                            codec=callback_codec,
                            order=updated,
                            new_status=status,
                            note="bulk update",
                        )
                state["selected"] = []
                await _save_admin_orders_state(container, session, state)
                await callback.answer(f"Обновлено: {changed}")
            elif payload == "edit":
                selected = list(state.get("selected", []))
                if len(selected) != 1:
                    await callback.answer("Для редактирования выберите ровно 1 заказ", show_alert=True)
                    return
                state["edit_order"] = selected[0]
                state["edit_field"] = None
                state["bulk_field"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer()
                await _send_order_edit_panel(
                    callback.message,
                    container=container,
                    user_id=callback.from_user.id,
                    codec=callback_codec,
                    state=state,
                    edit=True,
                )
                return
            elif payload.startswith("edit_field:"):
                field = payload.split(":", maxsplit=1)[1]
                if not state.get("edit_order"):
                    await callback.answer("Сначала выберите заказ", show_alert=True)
                    return
                state["edit_field"] = field
                state["pending_field"] = None
                state["pending_value"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer()
                order = await container.order_admin_service.get_order(str(state.get("edit_order")))
                current_value = _order_field_value(order, field) if order else "—"
                await edit_panel_message(callback.message, text=
                    f"Введите новое значение для поля: {_field_title(field)}\n"
                    f"Текущее: <code>{_h(current_value)}</code>",
                    parse_mode="HTML",
                )
                return
            elif payload == "confirm_apply":
                order_number = str(state.get("edit_order") or "")
                field = str(state.get("pending_field") or "")
                value = str(state.get("pending_value") or "")
                if not order_number or not field:
                    await callback.answer("Нет подготовленного изменения", show_alert=True)
                    return
                try:
                    updated = await container.order_admin_service.update_order_field(
                        order_number=order_number,
                        field_name=field,
                        raw_value=value,
                    )
                except Exception:
                    await callback.answer("Не удалось сохранить", show_alert=True)
                    return
                if not updated:
                    await callback.answer("Заказ не найден", show_alert=True)
                    return
                state["pending_field"] = None
                state["pending_value"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer("Изменение сохранено")
                await _send_order_edit_panel(
                    callback.message,
                    container=container,
                    user_id=callback.from_user.id,
                    codec=callback_codec,
                    state=state,
                    edit=True,
                )
                return
            elif payload == "confirm_cancel":
                state["pending_field"] = None
                state["pending_value"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer("Изменение отменено")
                await _send_order_edit_panel(
                    callback.message,
                    container=container,
                    user_id=callback.from_user.id,
                    codec=callback_codec,
                    state=state,
                    edit=True,
                )
                return
            elif payload.startswith("bulk_field:"):
                field = payload.split(":", maxsplit=1)[1]
                selected = list(state.get("selected", []))
                if not selected:
                    await callback.answer("Выберите заказы для массового редактирования", show_alert=True)
                    return
                state["bulk_field"] = field
                state["edit_field"] = None
                state["edit_order"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer()
                await edit_panel_message(callback.message, text=
                    f"Введите новое значение для поля `{_field_title(field)}`. "
                    f"Будет применено к {len(selected)} заказам.",
                    parse_mode="Markdown",
                )
                return
            elif payload == "back_list":
                state["edit_order"] = None
                state["edit_field"] = None
                state["bulk_field"] = None
                state["pending_field"] = None
                state["pending_value"] = None
                state["search_results"] = None
                state["awaiting_order_search_query"] = False
                state["order_search_mode"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer()
            elif payload == "clear":
                state["selected"] = []
                state["edit_order"] = None
                state["edit_field"] = None
                state["bulk_field"] = None
                state["pending_field"] = None
                state["pending_value"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer("Выбор очищен")

            await _send_orders_panel(callback.message, container, callback_codec, callback.from_user.id, state, edit=True)
            return


        raise SkipHandler
