from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.callback_panel import edit_panel_message
from app.bot.telegram.message_html import extract_caption_html, extract_message_html
from app.core.container import AppContainer
from app.services.admin_tools_service import (
    GroupTopicsStore,
    ProhibitedGoodsStore,
    StaticContentStore,
    send_content_with_media_to_telegram,
)

SCREEN_VIEW = "view"
SCREEN_EDIT_MENU = "edit_menu"
SCREEN_EDIT_TEXT = "edit_text"
SCREEN_EDIT_MEDIA = "edit_media"

CONTENT_UTILS_STATE_KEYS = (
    "content_utils_kind",
    "content_utils_screen",
    "content_utils_panel_chat_id",
    "content_utils_panel_message_id",
    "awaiting_content_utils_media",
)


def content_utils_has_waiter(utils_state: dict) -> bool:
    return str(utils_state.get("content_utils_screen") or "") == SCREEN_EDIT_TEXT


def reset_content_utils_state(utils_state: dict) -> None:
    for key in CONTENT_UTILS_STATE_KEYS:
        utils_state[key] = None


def _encode(codec: CallbackCodec, user_id: int, kind: str, suffix: str) -> str:
    return codec.encode(f"admin:utils:{kind}:{suffix}", user_id)


def _store_for_kind(
    kind: str,
    *,
    prohibited_store: ProhibitedGoodsStore,
    contacts_store: StaticContentStore,
):
    if kind == "prohibited":
        return prohibited_store
    if kind == "contacts":
        return contacts_store
    raise ValueError(f"Unknown content kind: {kind}")


def _section_meta(kind: str) -> tuple[str, str]:
    if kind == "prohibited":
        return "🚫 Запрещенка", "Запрещенные товары"
    if kind == "contacts":
        return "☎️ Контакты", "Наши контакты"
    raise ValueError(f"Unknown content kind: {kind}")


def _enter_edit_mode(utils_state: dict, kind: str) -> None:
    utils_state["content_utils_kind"] = kind
    utils_state["content_utils_screen"] = SCREEN_EDIT_TEXT
    utils_state["awaiting_content_utils_media"] = None


def enter_content_utils_edit_mode(utils_state: dict, kind: str) -> None:
    _enter_edit_mode(utils_state, kind)


def content_utils_edit_kind(utils_state: dict) -> str | None:
    kind = str(utils_state.get("content_utils_kind") or "")
    if kind not in {"prohibited", "contacts"}:
        return None
    if str(utils_state.get("content_utils_screen") or "") == SCREEN_EDIT_TEXT:
        return kind
    return None


def _extract_incoming_media(message: Message) -> tuple[str, str]:
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.video:
        return "video", message.video.file_id
    if message.animation:
        return "animation", message.animation.file_id
    if message.document:
        return "document", message.document.file_id
    return "", ""


async def _publish_panel_message(
    anchor: Message,
    *,
    utils_state: dict,
    text: str,
    media_items: list[dict],
    keyboard: InlineKeyboardMarkup,
) -> None:
    chat_id = int(utils_state.get("content_utils_panel_chat_id") or anchor.chat.id)
    old_message_id = utils_state.get("content_utils_panel_message_id")

    sent = await send_content_with_media_to_telegram(
        anchor,
        text=text,
        media_items=media_items,
        reply_markup=keyboard,
    )
    if sent is None:
        sent = await anchor.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    utils_state["content_utils_panel_chat_id"] = int(getattr(getattr(sent, "chat", None), "id", None) or chat_id)
    utils_state["content_utils_panel_message_id"] = int(sent.message_id)

    if old_message_id and int(old_message_id) != int(sent.message_id):
        try:
            await anchor.bot.delete_message(chat_id, int(old_message_id))
        except Exception:
            pass


async def open_content_utils_panel(
    message: Message,
    *,
    kind: str,
    codec: CallbackCodec,
    user_id: int,
    utils_state: dict,
    prohibited_store: ProhibitedGoodsStore,
    contacts_store: StaticContentStore,
) -> None:
    reset_content_utils_state(utils_state)
    utils_state["content_utils_kind"] = kind
    utils_state["content_utils_screen"] = SCREEN_VIEW
    utils_state["content_utils_panel_chat_id"] = int(message.chat.id)
    await refresh_content_utils_panel(
        message=message,
        codec=codec,
        user_id=user_id,
        utils_state=utils_state,
        prohibited_store=prohibited_store,
        contacts_store=contacts_store,
        force_new=True,
    )


async def refresh_content_utils_panel(
    *,
    message: Message,
    codec: CallbackCodec,
    user_id: int,
    utils_state: dict,
    prohibited_store: ProhibitedGoodsStore,
    contacts_store: StaticContentStore,
    force_new: bool = False,
) -> None:
    kind = str(utils_state.get("content_utils_kind") or "")
    if kind not in {"prohibited", "contacts"}:
        return

    store = _store_for_kind(
        kind,
        prohibited_store=prohibited_store,
        contacts_store=contacts_store,
    )
    screen = str(utils_state.get("content_utils_screen") or SCREEN_VIEW)
    media_items: list[dict] = []
    if screen != SCREEN_EDIT_TEXT:
        media_items = await store.get_media_items()

    text, keyboard = await _build_panel(
        utils_state=utils_state,
        codec=codec,
        user_id=user_id,
        prohibited_store=prohibited_store,
        contacts_store=contacts_store,
    )
    chat_id = int(utils_state.get("content_utils_panel_chat_id") or message.chat.id)
    panel_message_id = utils_state.get("content_utils_panel_message_id")

    if force_new or media_items or screen == SCREEN_EDIT_TEXT:
        await _publish_panel_message(
            message,
            utils_state=utils_state,
            text=text,
            media_items=media_items,
            keyboard=keyboard,
        )
        return

    if panel_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(panel_message_id),
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return
        except TelegramBadRequest as exc:
            error_text = str(exc).lower()
            if "message is not modified" in error_text:
                return

    if panel_message_id and int(panel_message_id) == int(message.message_id):
        try:
            await edit_panel_message(
                message,
                text=text,
                reply_markup=keyboard,
            )
            return
        except TelegramBadRequest:
            pass

    await _publish_panel_message(
        message,
        utils_state=utils_state,
        text=text,
        media_items=media_items,
        keyboard=keyboard,
    )


async def handle_content_utils_callback(
    callback: CallbackQuery,
    *,
    utils_action: str,
    codec: CallbackCodec,
    utils_state: dict,
    prohibited_store: ProhibitedGoodsStore,
    contacts_store: StaticContentStore,
) -> bool:
    if not callback.from_user or not callback.message:
        return False

    kind: str | None = None
    suffix = ""
    if utils_action.startswith("prohibited"):
        kind = "prohibited"
        suffix = utils_action[len("prohibited") :].lstrip(":")
    elif utils_action.startswith("contacts"):
        kind = "contacts"
        suffix = utils_action[len("contacts") :].lstrip(":")
    else:
        return False

    user_id = callback.from_user.id

    if not suffix:
        reset_content_utils_state(utils_state)
        utils_state["content_utils_kind"] = kind
        utils_state["content_utils_screen"] = SCREEN_VIEW
        utils_state["content_utils_panel_chat_id"] = int(callback.message.chat.id)
        utils_state["content_utils_panel_message_id"] = int(callback.message.message_id)
        await callback.answer()
        await refresh_content_utils_panel(
            message=callback.message,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            prohibited_store=prohibited_store,
            contacts_store=contacts_store,
            force_new=False,
        )
        return True

    if suffix in {"edit", "text", "media", "media_done"}:
        _enter_edit_mode(utils_state, kind)
        utils_state["content_utils_panel_chat_id"] = int(callback.message.chat.id)
        utils_state["content_utils_panel_message_id"] = int(callback.message.message_id)
        await callback.answer()
        await refresh_content_utils_panel(
            message=callback.message,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            prohibited_store=prohibited_store,
            contacts_store=contacts_store,
        )
        return True

    if suffix == "clear":
        store = _store_for_kind(
            kind,
            prohibited_store=prohibited_store,
            contacts_store=contacts_store,
        )
        await store.save_text("")
        await store.clear_media()
        utils_state["content_utils_screen"] = SCREEN_VIEW
        await callback.answer("Контент очищен")
        await refresh_content_utils_panel(
            message=callback.message,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            prohibited_store=prohibited_store,
            contacts_store=contacts_store,
        )
        return True

    if suffix == "back":
        await _handle_back(
            callback,
            kind=kind,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            prohibited_store=prohibited_store,
            contacts_store=contacts_store,
        )
        return True

    return False


async def try_handle_content_utils_submission(
    message: Message,
    *,
    codec: CallbackCodec,
    utils_state: dict,
    prohibited_store: ProhibitedGoodsStore,
    contacts_store: StaticContentStore,
    group_topics_store: GroupTopicsStore,
    container: AppContainer,
) -> bool:
    kind = content_utils_edit_kind(utils_state)
    if not kind or not message.from_user:
        return False

    media_type, file_id = _extract_incoming_media(message)
    if media_type and file_id:
        html_text = extract_caption_html(message)
    elif message.text:
        html_text = extract_message_html(message)
    else:
        return False

    if not html_text and not media_type:
        await message.answer("Отправьте текст или медиа с подписью.")
        return True

    store = _store_for_kind(
        kind,
        prohibited_store=prohibited_store,
        contacts_store=contacts_store,
    )

    from app.bot.telegram.handlers.admin.group_topics import _archive_media_in_group_topic
    from app.bot.telegram.handlers.admin.vk_sync import _sync_vk_attachment_from_tg

    if media_type and file_id:
        if html_text:
            await store.save_text(html_text)
        archive_chat_id = None
        archive_topic_id = None
        archive_message_id = None
        vk_attachment = None
        try:
            archive_chat_id, archive_topic_id, archive_message_id = await _archive_media_in_group_topic(
                message=message,
                group_topics_store=group_topics_store,
                label=f"{kind}_media",
            )
        except Exception:
            pass
        try:
            vk_attachment = await _sync_vk_attachment_from_tg(
                message=message,
                container=container,
                media_type=media_type,
                file_id=file_id,
            )
        except Exception:
            pass
        await store.replace_media(
            [
                {
                    "media_type": media_type,
                    "file_id": file_id,
                    "caption": "",
                    "vk_attachment": vk_attachment,
                    "storage_chat_id": archive_chat_id,
                    "storage_topic_id": archive_topic_id,
                    "storage_message_id": archive_message_id,
                }
            ]
        )
    else:
        await store.save_text(html_text)
        await store.clear_media()

    utils_state["content_utils_screen"] = SCREEN_VIEW
    utils_state["awaiting_content_utils_media"] = None

    try:
        await refresh_content_utils_panel(
            message=message,
            codec=codec,
            user_id=message.from_user.id,
            utils_state=utils_state,
            prohibited_store=prohibited_store,
            contacts_store=contacts_store,
        )
    except Exception:
        await message.answer("Сохранено, но не удалось обновить панель. Откройте раздел снова.")
        return True

    await message.answer("✅ Сохранено")
    return True


async def try_handle_content_utils_text(
    message: Message,
    *,
    codec: CallbackCodec,
    utils_state: dict,
    prohibited_store: ProhibitedGoodsStore,
    contacts_store: StaticContentStore,
    group_topics_store: GroupTopicsStore | None = None,
    container: AppContainer | None = None,
) -> bool:
    if not message.text or group_topics_store is None or container is None:
        return False
    return await try_handle_content_utils_submission(
        message,
        codec=codec,
        utils_state=utils_state,
        prohibited_store=prohibited_store,
        contacts_store=contacts_store,
        group_topics_store=group_topics_store,
        container=container,
    )


async def _handle_back(
    callback: CallbackQuery,
    *,
    kind: str,
    codec: CallbackCodec,
    user_id: int,
    utils_state: dict,
    prohibited_store: ProhibitedGoodsStore,
    contacts_store: StaticContentStore,
) -> None:
    screen = str(utils_state.get("content_utils_screen") or SCREEN_VIEW)
    if screen in {SCREEN_EDIT_MENU, SCREEN_EDIT_TEXT, SCREEN_EDIT_MEDIA}:
        utils_state["content_utils_screen"] = SCREEN_VIEW
        utils_state["awaiting_content_utils_media"] = None
        await callback.answer()
        await refresh_content_utils_panel(
            message=callback.message,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            prohibited_store=prohibited_store,
            contacts_store=contacts_store,
        )
        return

    utils_state["content_utils_kind"] = None
    utils_state["content_utils_screen"] = None
    utils_state["awaiting_content_utils_media"] = None
    await callback.answer()
    from app.bot.telegram.handlers.admin.keyboards import UTILS_PANEL_TEXT, _utils_inline_keyboard

    await edit_panel_message(
        callback.message,
        text=UTILS_PANEL_TEXT,
        reply_markup=_utils_inline_keyboard(user_id, codec),
    )


def _view_keyboard(kind: str, codec: CallbackCodec, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Редактировать", callback_data=_encode(codec, user_id, kind, "edit"))],
            [
                InlineKeyboardButton(
                    text="Очистить контент",
                    callback_data=_encode(codec, user_id, kind, "clear"),
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, kind, "back"))],
        ]
    )


def _edit_keyboard(kind: str, codec: CallbackCodec, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, kind, "back"))],
        ]
    )


async def _build_panel(
    *,
    utils_state: dict,
    codec: CallbackCodec,
    user_id: int,
    prohibited_store: ProhibitedGoodsStore,
    contacts_store: StaticContentStore,
) -> tuple[str, InlineKeyboardMarkup]:
    kind = str(utils_state.get("content_utils_kind") or "")
    screen = str(utils_state.get("content_utils_screen") or SCREEN_VIEW)
    title, _ = _section_meta(kind)
    store = _store_for_kind(
        kind,
        prohibited_store=prohibited_store,
        contacts_store=contacts_store,
    )
    body = await store.get_text()
    preview = body.strip() if body.strip() else "—"

    if screen == SCREEN_EDIT_TEXT:
        text = (
            f"{title}\n\n"
            "<b>Отправьте новый контент</b>\n\n"
            "Одним сообщением — оно полностью заменит текущий текст и медиа:\n"
            "• только текст\n"
            "• фото / видео / GIF с подписью (подпись = текст раздела)"
        )
        return text, _edit_keyboard(kind, codec, user_id)

    text = f"{title}\n\n{preview}"
    return text, _view_keyboard(kind, codec, user_id)
