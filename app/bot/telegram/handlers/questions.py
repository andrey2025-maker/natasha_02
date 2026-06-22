from __future__ import annotations

import asyncio
import logging
from html import escape

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReactionTypeEmoji

from app.bot.telegram.callbacks import CallbackAuthError, CallbackCodec
from app.bot.telegram.callback_panel import edit_content_with_media, edit_panel_message, message_has_media
from app.bot.telegram.bot_api import api_copy_message, api_send_message
from app.bot.telegram.handlers.admin import clear_admin_state_on_menu_nav
from app.bot.telegram.mirror_bot import skip_dialog_mirror
from app.bot.telegram.user_access import is_user_blocked_by_admin
from app.core.container import AppContainer
from app.domain.enums import Platform
from app.services.admin_tools_service import (
    FaqMediaStore,
    ProhibitedGoodsStore,
    QuestionsAlertStore,
    StaticContentStore,
    TopicDialogStore,
    send_content_with_media_to_telegram,
)
from app.bot.telegram.handlers.questions_topic import handle_questions_process_callback

logger = logging.getLogger(__name__)


def build_questions_router(container: AppContainer) -> Router:
    router = Router()
    callback_codec = CallbackCodec(container.callback_signer)
    prohibited_store = ProhibitedGoodsStore(container.settings.database.dsn)
    topic_dialog_store = TopicDialogStore(container.settings.database.dsn)
    questions_alert_store = QuestionsAlertStore(container.settings.database.dsn)
    faq_media_store = FaqMediaStore(container.settings.database.dsn)
    delivery_store = StaticContentStore(
        database_dsn=container.settings.database.dsn,
        key="delivery_info",
        default_text="Раздел о доставке пока не заполнен.",
    )
    contacts_store = StaticContentStore(
        database_dsn=container.settings.database.dsn,
        key="contacts_info",
        default_text="Раздел контактов пока не заполнен.",
    )

    async def _is_blocked_user(user_id: int) -> bool:
        return await is_user_blocked_by_admin(container, user_id)

    def _schedule_clear_admin_menu_state(message: Message) -> None:
        if not message.from_user:
            return

        async def _clear() -> None:
            try:
                await clear_admin_state_on_menu_nav(
                    container,
                    platform=Platform.TELEGRAM,
                    user_id=message.from_user.id,
                )
            except Exception:
                logger.exception(
                    "Failed to clear admin menu state for user_id=%s",
                    message.from_user.id,
                )

        asyncio.create_task(_clear())

    @router.message(F.chat.type == "private", F.text.in_({"Запрещенные товары", "🚫 Запрещенные товары"}))
    async def prohibited_goods(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        panel = await message.answer("🚫 <i>Загрузка…</i>", parse_mode="HTML")
        _schedule_clear_admin_menu_state(message)
        asyncio.create_task(_finalize_static_content(message, panel, prohibited_store))

    @router.message(F.chat.type == "private", F.text.in_({"Как работает доставка", "🚚 Как работает доставка"}))
    async def delivery_info(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        panel = await message.answer("🚚 <i>Загрузка…</i>", parse_mode="HTML")
        _schedule_clear_admin_menu_state(message)
        asyncio.create_task(_finalize_static_content(message, panel, delivery_store))

    @router.message(F.chat.type == "private", F.text.in_({"Наши контакты", "☎️ Наши контакты"}))
    async def contacts_info(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        panel = await message.answer("☎️ <i>Загрузка…</i>", parse_mode="HTML")
        _schedule_clear_admin_menu_state(message)
        asyncio.create_task(_finalize_static_content(message, panel, contacts_store))

    @router.message(F.chat.type == "private", F.text.in_({"Вопросы", "❓ Вопросы"}))
    async def faq_root(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        panel = await message.answer("❓ <i>Загрузка…</i>", parse_mode="HTML")
        _schedule_clear_admin_menu_state(message)
        asyncio.create_task(
            _finalize_faq_root(
                message=message,
                panel=panel,
                user_id=message.from_user.id,
                container=container,
                codec=callback_codec,
                faq_media_store=faq_media_store,
            )
        )

    @router.message(F.chat.type.in_({"group", "supergroup"}), F.reply_to_message, F.text)
    async def manager_text_reply_in_topic(message: Message) -> None:
        if not message.from_user or not message.text or not message.reply_to_message:
            raise SkipHandler
        if message.text.startswith("/"):
            raise SkipHandler
        if not await container.admin_service.is_admin(message.from_user.id):
            raise SkipHandler
        relayed = await _relay_topic_reply_to_user(
            message=message,
            container=container,
            topic_dialog_store=topic_dialog_store,
            as_media=False,
        )
        if not relayed:
            raise SkipHandler

    @router.message(F.chat.type.in_({"group", "supergroup"}), F.reply_to_message, F.photo | F.video | F.animation | F.document)
    async def manager_media_reply_in_topic(message: Message) -> None:
        if not message.from_user or not message.reply_to_message:
            raise SkipHandler
        if not await container.admin_service.is_admin(message.from_user.id):
            raise SkipHandler
        relayed = await _relay_topic_reply_to_user(
            message=message,
            container=container,
            topic_dialog_store=topic_dialog_store,
            as_media=True,
        )
        if not relayed:
            raise SkipHandler

    @router.message(F.chat.type.in_({"group", "supergroup"}), F.text)
    async def manager_text_in_topic(message: Message) -> None:
        if not message.from_user or not message.text:
            raise SkipHandler
        if message.text.startswith("/"):
            raise SkipHandler
        if not await container.admin_service.is_admin(message.from_user.id):
            raise SkipHandler
        relayed = await _relay_topic_reply_to_user(
            message=message,
            container=container,
            topic_dialog_store=topic_dialog_store,
            as_media=False,
        )
        if not relayed:
            raise SkipHandler

    @router.message(F.chat.type.in_({"group", "supergroup"}), F.photo | F.video | F.animation | F.document)
    async def manager_media_in_topic(message: Message) -> None:
        if not message.from_user:
            raise SkipHandler
        if not await container.admin_service.is_admin(message.from_user.id):
            raise SkipHandler
        relayed = await _relay_topic_reply_to_user(
            message=message,
            container=container,
            topic_dialog_store=topic_dialog_store,
            as_media=True,
        )
        if not relayed:
            raise SkipHandler

    @router.callback_query()
    async def questions_topic_callbacks(callback: CallbackQuery) -> None:
        if not callback.data:
            raise SkipHandler
        try:
            action = callback_codec.decode_public(callback.data)
        except CallbackAuthError:
            raise SkipHandler
        if not action.startswith("questions:"):
            raise SkipHandler
        handled = await handle_questions_process_callback(
            callback,
            action=action,
            questions_alert_store=questions_alert_store,
            callback_codec=callback_codec,
        )
        if not handled:
            raise SkipHandler

    @router.callback_query()
    async def faq_callbacks(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.data or not callback.message:
            raise SkipHandler
        if await _is_blocked_user(callback.from_user.id):
            await callback.answer("Доступ ограничен", show_alert=True)
            return
        try:
            action = callback_codec.decode(callback.data, callback.from_user.id)
        except CallbackAuthError:
            raise SkipHandler
        if not action.startswith("faq:"):
            raise SkipHandler

        raw_section = action.split(":", maxsplit=1)[1]
        if raw_section == "root":
            section_id = None
        else:
            try:
                section_id = int(raw_section)
            except ValueError:
                await callback.answer("Неверный раздел", show_alert=True)
                return

        await callback.answer()
        await _send_section(
            message=callback.message,
            user_id=callback.from_user.id,
            container=container,
            codec=callback_codec,
            faq_media_store=faq_media_store,
            section_id=section_id,
            edit=True,
        )

    return router


async def _finalize_faq_root(
    *,
    message: Message,
    panel: Message,
    user_id: int,
    container: AppContainer,
    codec: CallbackCodec,
    faq_media_store: FaqMediaStore,
) -> None:
    try:
        await _send_section(
            message=panel,
            user_id=user_id,
            container=container,
            codec=codec,
            faq_media_store=faq_media_store,
            section_id=None,
            edit=True,
        )
    except Exception:
        logger.exception("Failed to load FAQ root for user_id=%s", user_id)


async def _finalize_static_content(
    message: Message,
    panel: Message,
    store: StaticContentStore,
) -> None:
    try:
        text, media_items = await asyncio.gather(store.get_text(), store.get_media_items())
        if media_items:
            try:
                await panel.delete()
            except Exception:
                pass
            await send_content_with_media_to_telegram(
                message,
                text=text,
                media_items=media_items,
            )
            return
        await edit_panel_message(panel, text=text)
    except Exception:
        logger.exception("Failed to load static content for chat_id=%s", message.chat.id)


async def _send_section(
    message: Message,
    user_id: int,
    container: AppContainer,
    codec: CallbackCodec,
    faq_media_store: FaqMediaStore,
    section_id: int | None,
    edit: bool,
) -> None:
    if section_id is not None:
        current, children, path_text, media_items = await asyncio.gather(
            container.faq_service.get_section(section_id),
            container.faq_service.list_children(section_id),
            container.faq_service.breadcrumbs(section_id),
            faq_media_store.get_media_items(section_id),
        )
    else:
        current = None
        children, path_text = await asyncio.gather(
            container.faq_service.list_children(section_id),
            container.faq_service.breadcrumbs(section_id),
        )
        media_items = []

    body_lines = [f"<b>{escape(path_text, quote=False)}</b>"]
    if current and current.content_text:
        body_lines.append(escape(current.content_text, quote=False))
    if children:
        body_lines.append("")
        body_lines.append("Выберите раздел:")
    elif not current or not current.content_text:
        body_lines.append("Раздел пока пуст.")

    parent_id = current.parent_id if current else None
    keyboard = _faq_keyboard(
        user_id=user_id,
        codec=codec,
        section_id=section_id,
        parent_id=parent_id,
        children=children,
    )
    text = "\n".join(body_lines)

    if media_items:
        await edit_content_with_media(
            message,
            text=text,
            media_items=media_items,
            reply_markup=keyboard,
        )
        return

    if edit:
        if message_has_media(message):
            try:
                await message.delete()
            except Exception:
                pass
            await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
            return
        await edit_panel_message(
            message,
            text=text,
            reply_markup=keyboard,
        )
        return

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def _send_static_content(message: Message, store: StaticContentStore) -> None:
    text = await store.get_text()
    media_items = await store.get_media_items()
    await send_content_with_media_to_telegram(
        message,
        text=text,
        media_items=media_items,
    )


def _faq_keyboard(
    user_id: int,
    codec: CallbackCodec,
    section_id: int | None,
    parent_id: int | None,
    children,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for child in children:
        rows.append(
            [
                InlineKeyboardButton(
                    text=child.title,
                    callback_data=codec.encode(f"faq:{child.id}", user_id),
                )
            ]
        )
    if section_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=codec.encode(f"faq:{parent_id if parent_id is not None else 'root'}", user_id),
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🏠 В начало",
                    callback_data=codec.encode("faq:root", user_id),
                )
            ]
        )
    if not rows:
        rows = [[InlineKeyboardButton(text="🏠 В начало", callback_data=codec.encode("faq:root", user_id))]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _relay_topic_reply_to_user(
    message: Message,
    container: AppContainer,
    topic_dialog_store: TopicDialogStore,
    as_media: bool,
) -> bool:
    platform_user = None
    if message.reply_to_message:
        platform_user = await topic_dialog_store.resolve_user_by_topic_message(
            chat_id=int(message.chat.id),
            topic_id=message.message_thread_id,
            topic_message_id=int(message.reply_to_message.message_id),
        )
    if not platform_user:
        platform_user = await topic_dialog_store.resolve_user_by_topic(
            chat_id=int(message.chat.id),
            topic_id=message.message_thread_id,
        )
    if not platform_user:
        return False
    platform, target_user_id = platform_user
    if platform != Platform.TELEGRAM.value:
        return False
    try:
        async with skip_dialog_mirror():
            if as_media:
                await api_copy_message(
                    message.bot,
                    chat_id=target_user_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )
            else:
                text = message.text or ""
                if not text.strip():
                    return False
                await api_send_message(
                    message.bot,
                    chat_id=target_user_id,
                    text=text,
                    parse_mode=None,
                )
    except Exception as exc:
        await _mark_blocked_bot_if_needed(container, target_user_id, exc)
        return False
    await topic_dialog_store.bind_topic_message_to_user(
        chat_id=int(message.chat.id),
        topic_id=message.message_thread_id,
        topic_message_id=int(message.message_id),
        platform=Platform.TELEGRAM.value,
        platform_user_id=target_user_id,
    )
    if message.reply_to_message:
        await _mark_relay_delivered_in_topic(message)
    return True


async def _mark_relay_delivered_in_topic(message: Message) -> None:
    try:
        await message.bot.set_message_reaction(
            chat_id=int(message.chat.id),
            message_id=int(message.message_id),
            reaction=[ReactionTypeEmoji(emoji="👍")],
            is_big=True,
        )
    except Exception:
        return


async def _mark_blocked_bot_if_needed(container: AppContainer, telegram_user_id: int, error: Exception) -> None:
    if not isinstance(error, TelegramForbiddenError):
        return
    profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, telegram_user_id)
    if profile and not profile.blocked_bot:
        profile.blocked_bot = True
        await container.profile_repo.save(profile)
