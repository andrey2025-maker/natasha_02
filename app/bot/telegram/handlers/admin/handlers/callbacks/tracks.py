from __future__ import annotations

from aiogram import Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import CallbackQuery

from app.bot.telegram.callbacks import CallbackAuthError
from app.bot.telegram.callback_panel import edit_panel_message
from app.bot.telegram.handlers.admin.context import AdminContext
from app.bot.telegram.handlers.admin.tracks import (
    _get_admin_tracks_state,
    _save_admin_tracks_state,
    _tracks_back_keyboard,
    _tracks_comment_keyboard,
    _tracks_status_keyboard,
    apply_tracks_status_update,
    open_admin_tracks_panel,
    reset_admin_tracks_state,
    tracks_comment_prompt_text,
    tracks_done_text,
    tracks_scanner_text,
    tracks_txt_prompt_text,
)
from app.bot.telegram.handlers.admin.orders import _parse_order_status
from app.domain.enums import Platform
from app.bot.telegram.handlers.admin.panel import edit_admin_panel


def register_tracks_callbacks(router: Router, ctx: AdminContext) -> None:
    container = ctx.container
    callback_codec = ctx.callback_codec
    payment_store = ctx.payment_store

    @router.callback_query()
    async def admin_tracks_callbacks(callback: CallbackQuery) -> None:
        if not callback.data or not callback.from_user or not callback.message:
            raise SkipHandler
        if not await container.admin_service.is_admin(callback.from_user.id):
            raise SkipHandler
        try:
            action = callback_codec.decode(callback.data, callback.from_user.id)
        except CallbackAuthError:
            raise SkipHandler
        if not action.startswith("admin:tracks:"):
            raise SkipHandler

        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
        tracks_state = _get_admin_tracks_state(session)
        payload = action.split(":", maxsplit=2)[2]

        if payload == "back":
            reset_admin_tracks_state(tracks_state)
            await _save_admin_tracks_state(container, session, tracks_state)
            await callback.answer()
            await edit_admin_panel(
                callback.message,
                container=container,
                user_id=callback.from_user.id,
                callback_codec=callback_codec,
            )
            return

        if payload == "root":
            reset_admin_tracks_state(tracks_state)
            await _save_admin_tracks_state(container, session, tracks_state)
            await callback.answer()
            await open_admin_tracks_panel(
                callback.message,
                user_id=callback.from_user.id,
                codec=callback_codec,
            )
            return

        if payload == "txt":
            reset_admin_tracks_state(tracks_state)
            tracks_state["awaiting_txt_file"] = True
            await _save_admin_tracks_state(container, session, tracks_state)
            await callback.answer()
            await edit_panel_message(
                callback.message,
                text=tracks_txt_prompt_text(),
                reply_markup=_tracks_back_keyboard(callback.from_user.id, callback_codec),
            )
            return

        if payload == "scanner":
            await callback.answer()
            await edit_panel_message(
                callback.message,
                text=tracks_scanner_text(),
                reply_markup=_tracks_back_keyboard(callback.from_user.id, callback_codec),
            )
            return

        if payload.startswith("set_status:"):
            status = _parse_order_status(payload.split(":", maxsplit=1)[1])
            if status is None:
                await callback.answer("Неизвестный статус", show_alert=True)
                return
            if not tracks_state.get("matched_order_numbers"):
                await callback.answer("Сначала загрузите TXT с треками.", show_alert=True)
                return
            tracks_state["pending_status"] = status.value
            tracks_state["awaiting_status_comment"] = False
            await _save_admin_tracks_state(container, session, tracks_state)
            await callback.answer()
            await edit_panel_message(
                callback.message,
                text=tracks_comment_prompt_text(),
                reply_markup=_tracks_comment_keyboard(callback.from_user.id, callback_codec),
            )
            return

        if payload == "comment:yes":
            status = _parse_order_status(str(tracks_state.get("pending_status") or ""))
            if status is None or not tracks_state.get("matched_order_numbers"):
                await callback.answer("Сессия устарела. Начните заново.", show_alert=True)
                return
            tracks_state["awaiting_status_comment"] = True
            await _save_admin_tracks_state(container, session, tracks_state)
            await callback.answer()
            await edit_panel_message(
                callback.message,
                text="Введите комментарий к статусу одним сообщением.",
                reply_markup=_tracks_back_keyboard(callback.from_user.id, callback_codec),
            )
            return

        if payload == "comment:no":
            status = _parse_order_status(str(tracks_state.get("pending_status") or ""))
            if status is None or not tracks_state.get("matched_order_numbers"):
                await callback.answer("Сессия устарела. Начните заново.", show_alert=True)
                return
            changed = await apply_tracks_status_update(
                trigger=callback,
                container=container,
                payment_store=payment_store,
                codec=callback_codec,
                session=session,
                status=status,
                note="",
            )
            await callback.answer()
            await edit_panel_message(
                callback.message,
                text=tracks_done_text(changed=changed, status=status),
                reply_markup=_tracks_back_keyboard(callback.from_user.id, callback_codec),
            )
            return

        raise SkipHandler
