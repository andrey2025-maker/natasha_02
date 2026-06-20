from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.callback_panel import edit_content_with_media, edit_panel_message
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
        profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, user_id)
        return bool(profile and profile.is_blocked_by_admin)

    @router.message(F.chat.type == "private", F.text.in_({"Запрещенные товары", "🚫 Запрещенные товары"}))
    async def prohibited_goods(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        await _send_static_content(message, prohibited_store)

    @router.message(F.chat.type == "private", F.text.in_({"Как работает доставка", "🚚 Как работает доставка"}))
    async def delivery_info(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        await _send_static_content(message, delivery_store)

    @router.message(F.chat.type == "private", F.text.in_({"Наши контакты", "☎️ Наши контакты"}))
    async def contacts_info(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        await _send_static_content(message, contacts_store)

    @router.message(F.chat.type == "private", F.text.in_({"Вопросы", "❓ Вопросы"}))
    async def faq_root(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        await _send_section(
            message=message,
            user_id=message.from_user.id,
            container=container,
            codec=callback_codec,
            faq_media_store=faq_media_store,
            section_id=None,
            edit=False,
        )

    @router.message(F.chat.type.in_({"group", "supergroup"}), F.reply_to_message, F.text)
    async def manager_text_reply_in_topic(message: Message) -> None:
        if not message.from_user or not message.text or not message.reply_to_message:
            return
        if message.text.startswith("/"):
            return
        if not await container.admin_service.is_admin(message.from_user.id):
            return
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
            return
        if not await container.admin_service.is_admin(message.from_user.id):
            return
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
            return
        if message.text.startswith("/"):
            return
        if not await container.admin_service.is_admin(message.from_user.id):
            return
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
            return
        if not await container.admin_service.is_admin(message.from_user.id):
            return
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
            return
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


async def _send_section(
    message: Message,
    user_id: int,
    container: AppContainer,
    codec: CallbackCodec,
    faq_media_store: FaqMediaStore,
    section_id: int | None,
    edit: bool,
) -> None:
    current = await container.faq_service.get_section(section_id) if section_id is not None else None
    children = await container.faq_service.list_children(section_id)
    path_text = await container.faq_service.breadcrumbs(section_id)

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

    media_items: list[dict] = []
    if section_id is not None:
        media_items = await faq_media_store.get_media_items(section_id)

    if media_items:
        await edit_content_with_media(
            message,
            text=text,
            media_items=media_items,
            reply_markup=keyboard,
        )
        return

    if edit:
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
                    text="🏠 К разделам",
                    callback_data=codec.encode("faq:root", user_id),
                )
            ]
        )
    if not rows:
        rows = [[InlineKeyboardButton(text="🏠 К разделам", callback_data=codec.encode("faq:root", user_id))]]
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
        if as_media:
            await message.bot.copy_message(
                chat_id=target_user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
        else:
            text = message.text or ""
            if not text.strip():
                return False
            await message.bot.send_message(
                chat_id=target_user_id,
                text=f"💬 Ответ менеджера:\n\n{text}",
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
    await message.reply("✅ Отправлено клиенту")
    return True


async def _mark_blocked_bot_if_needed(container: AppContainer, telegram_user_id: int, error: Exception) -> None:
    if not isinstance(error, TelegramForbiddenError):
        return
    profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, telegram_user_id)
    if profile and not profile.blocked_bot:
        profile.blocked_bot = True
        await container.profile_repo.save(profile)
