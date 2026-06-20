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
    SCREEN_EDIT_TEXT as CONTENT_UTILS_EDIT_TEXT,
    content_utils_edit_kind,
    enter_content_utils_edit_mode,
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

def register_media_messages(router: Router, ctx: AdminContext) -> None:
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

    @router.message(F.photo | F.video | F.animation | F.document)
    async def admin_broadcast_media_input(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        utils_state = _get_admin_utils_state(session)
        if utils_state.get("awaiting_payment_media"):
            media_type = ""
            file_id = ""
            if message.photo:
                media_type = "photo"
                file_id = message.photo[-1].file_id
            elif message.video:
                media_type = "video"
                file_id = message.video.file_id
            elif message.animation:
                media_type = "animation"
                file_id = message.animation.file_id
            elif message.document:
                media_type = "document"
                file_id = message.document.file_id
            if media_type and file_id:
                archive_chat_id, archive_topic_id, archive_message_id = await _archive_media_in_group_topic(
                    message=message,
                    group_topics_store=group_topics_store,
                    label="payment_media",
                )
                vk_attachment = await _sync_vk_attachment_from_tg(
                    message=message,
                    container=container,
                    media_type=media_type,
                    file_id=file_id,
                )
                await payment_store.save_media(
                    media_type=media_type,
                    file_id=file_id,
                    caption=message.caption or "",
                    vk_attachment=vk_attachment,
                    storage_chat_id=archive_chat_id,
                    storage_topic_id=archive_topic_id,
                    storage_message_id=archive_message_id,
                )
                await _save_admin_utils_state(container, session, utils_state)
                suffix = " и синхронизировано в VK." if vk_attachment else ". VK синхронизация не выполнена."
                await message.answer("Медиа-инструкция оплаты добавлена" + suffix + " Отправьте ещё или «Готово медиа».")
            return
        if utils_state.get("awaiting_delivery_media"):
            media_type = ""
            file_id = ""
            if message.photo:
                media_type = "photo"
                file_id = message.photo[-1].file_id
            elif message.video:
                media_type = "video"
                file_id = message.video.file_id
            elif message.animation:
                media_type = "animation"
                file_id = message.animation.file_id
            elif message.document:
                media_type = "document"
                file_id = message.document.file_id
            if media_type and file_id:
                archive_chat_id, archive_topic_id, archive_message_id = await _archive_media_in_group_topic(
                    message=message,
                    group_topics_store=group_topics_store,
                    label="delivery_media",
                )
                vk_attachment = await _sync_vk_attachment_from_tg(
                    message=message,
                    container=container,
                    media_type=media_type,
                    file_id=file_id,
                )
                await delivery_store.save_media(
                    media_type=media_type,
                    file_id=file_id,
                    caption=message.caption or "",
                    vk_attachment=vk_attachment,
                    storage_chat_id=archive_chat_id,
                    storage_topic_id=archive_topic_id,
                    storage_message_id=archive_message_id,
                )
                await _save_admin_utils_state(container, session, utils_state)
                suffix = " и синхронизировано в VK." if vk_attachment else ". VK синхронизация не выполнена."
                await message.answer("Медиа доставки добавлено" + suffix + " Отправьте ещё или «Готово медиа».")
            return
        content_utils_kind = content_utils_edit_kind(utils_state)
        if content_utils_kind in {"prohibited", "contacts"}:
            media_type = ""
            file_id = ""
            if message.photo:
                media_type = "photo"
                file_id = message.photo[-1].file_id
            elif message.video:
                media_type = "video"
                file_id = message.video.file_id
            elif message.animation:
                media_type = "animation"
                file_id = message.animation.file_id
            elif message.document:
                media_type = "document"
                file_id = message.document.file_id
            if not media_type or not file_id:
                await message.answer("Не удалось распознать медиа. Отправьте фото, видео, GIF или документ.")
                return
            label = f"{content_utils_kind}_media"
            store = prohibited_store if content_utils_kind == "prohibited" else contacts_store
            archive_chat_id, archive_topic_id, archive_message_id = await _archive_media_in_group_topic(
                message=message,
                group_topics_store=group_topics_store,
                label=label,
            )
            vk_attachment = await _sync_vk_attachment_from_tg(
                message=message,
                container=container,
                media_type=media_type,
                file_id=file_id,
            )
            await store.save_media(
                media_type=media_type,
                file_id=file_id,
                caption=message.caption or "",
                vk_attachment=vk_attachment,
                storage_chat_id=archive_chat_id,
                storage_topic_id=archive_topic_id,
                storage_message_id=archive_message_id,
            )
            enter_content_utils_edit_mode(utils_state, content_utils_kind)
            try:
                await refresh_content_utils_panel(
                    message=message,
                    codec=callback_codec,
                    user_id=message.from_user.id,
                    utils_state=utils_state,
                    prohibited_store=prohibited_store,
                    contacts_store=contacts_store,
                )
            except Exception:
                await message.answer(
                    "Медиа сохранено, но не удалось обновить панель. "
                    "Нажмите «⬅️ Назад» и откройте раздел снова."
                )
                await _save_admin_utils_state(container, session, utils_state)
                return
            await _save_admin_utils_state(container, session, utils_state)
            return
        if utils_state.get("awaiting_faq_media_section_id"):
            section_id = int(utils_state.get("awaiting_faq_media_section_id"))
            media_type = ""
            file_id = ""
            if message.photo:
                media_type = "photo"
                file_id = message.photo[-1].file_id
            elif message.video:
                media_type = "video"
                file_id = message.video.file_id
            elif message.animation:
                media_type = "animation"
                file_id = message.animation.file_id
            elif message.document:
                media_type = "document"
                file_id = message.document.file_id
            if media_type and file_id:
                archive_chat_id, archive_topic_id, archive_message_id = await _archive_media_in_group_topic(
                    message=message,
                    group_topics_store=group_topics_store,
                    label=f"faq_media_{section_id}",
                )
                vk_attachment = await _sync_vk_attachment_from_tg(
                    message=message,
                    container=container,
                    media_type=media_type,
                    file_id=file_id,
                )
                await faq_media_store.save_media(
                    section_id=int(section_id),
                    media_type=media_type,
                    file_id=file_id,
                    caption=message.caption or "",
                    vk_attachment=vk_attachment,
                    storage_chat_id=archive_chat_id,
                    storage_topic_id=archive_topic_id,
                    storage_message_id=archive_message_id,
                )
                await _save_admin_utils_state(container, session, utils_state)
                if str(utils_state.get("faq_admin_screen") or "") == SCREEN_EDIT_MEDIA:
                    await refresh_faq_admin_panel(
                        message=message,
                        container=container,
                        codec=callback_codec,
                        user_id=message.from_user.id,
                        utils_state=utils_state,
                        faq_media_store=faq_media_store,
                    )
                else:
                    suffix = " и синхронизировано в VK." if vk_attachment else ". VK синхронизация не выполнена."
                    await message.answer(
                        f"FAQ медиа (раздел {section_id}) добавлено{suffix} Отправьте ещё или «Готово медиа»."
                    )
            return

        state = _get_admin_broadcast_state(session)
        if not state.get("awaiting_payload"):
            return
        audience = str(state.get("audience") or "")
        if audience not in {"all", "active", "inactive"}:
            await message.answer("Сначала выберите аудиторию в разделе «Рассылка».")
            return

        tg_sent, tg_failed, vk_enqueued = await _dispatch_broadcast_media(
            message,
            container=container,
            backup_service=backup_service,
            audience=audience,
        )
        state["awaiting_payload"] = False
        state["audience"] = None
        await _save_admin_broadcast_state(container, session, state)
        await message.answer(
            "Медиа-рассылка поставлена в работу.\n"
            f"TG отправлено: {tg_sent}\n"
            f"TG ошибки: {tg_failed}\n"
            f"VK в очередь (текст-пояснение): {vk_enqueued}"
        )
