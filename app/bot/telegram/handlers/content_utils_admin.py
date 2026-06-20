from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.message_html import extract_message_html
from app.services.admin_tools_service import ProhibitedGoodsStore, StaticContentStore

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
    screen = str(utils_state.get("content_utils_screen") or "")
    if screen == SCREEN_EDIT_TEXT:
        return True
    if utils_state.get("awaiting_content_utils_media"):
        return True
    return False


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
    text, keyboard = await _build_panel(
        utils_state=utils_state,
        codec=codec,
        user_id=user_id,
        prohibited_store=prohibited_store,
        contacts_store=contacts_store,
    )
    chat_id = int(utils_state.get("content_utils_panel_chat_id") or message.chat.id)
    panel_message_id = utils_state.get("content_utils_panel_message_id")
    if not force_new and panel_message_id:
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
    sent = await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    utils_state["content_utils_panel_chat_id"] = int(sent.chat.id)
    utils_state["content_utils_panel_message_id"] = int(sent.message_id)


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
        await callback.answer()
        await refresh_content_utils_panel(
            message=callback.message,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            prohibited_store=prohibited_store,
            contacts_store=contacts_store,
            force_new=True,
        )
        return True

    if suffix == "edit":
        utils_state["content_utils_kind"] = kind
        utils_state["content_utils_screen"] = SCREEN_EDIT_MENU
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

    if suffix == "text":
        utils_state["content_utils_kind"] = kind
        utils_state["content_utils_screen"] = SCREEN_EDIT_TEXT
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

    if suffix == "media":
        utils_state["content_utils_kind"] = kind
        utils_state["content_utils_screen"] = SCREEN_EDIT_MEDIA
        utils_state["awaiting_content_utils_media"] = kind
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

    if suffix == "media_done":
        utils_state["content_utils_screen"] = SCREEN_EDIT_MENU
        utils_state["awaiting_content_utils_media"] = None
        await callback.answer("Сохранено")
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
        await store.clear_media()
        utils_state["content_utils_kind"] = kind
        utils_state["content_utils_screen"] = SCREEN_EDIT_MENU
        await callback.answer("Медиа очищено")
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


async def try_handle_content_utils_text(
    message: Message,
    *,
    codec: CallbackCodec,
    utils_state: dict,
    prohibited_store: ProhibitedGoodsStore,
    contacts_store: StaticContentStore,
) -> bool:
    if not message.from_user or not message.text:
        return False
    if str(utils_state.get("content_utils_screen") or "") != SCREEN_EDIT_TEXT:
        return False

    kind = str(utils_state.get("content_utils_kind") or "")
    if kind not in {"prohibited", "contacts"}:
        return False

    html_text = extract_message_html(message)
    if not html_text:
        await message.answer("Текст не может быть пустым.")
        return True

    store = _store_for_kind(
        kind,
        prohibited_store=prohibited_store,
        contacts_store=contacts_store,
    )
    await store.save_text(html_text)
    utils_state["content_utils_screen"] = SCREEN_EDIT_MENU
    await refresh_content_utils_panel(
        message=message,
        codec=codec,
        user_id=message.from_user.id,
        utils_state=utils_state,
        prohibited_store=prohibited_store,
        contacts_store=contacts_store,
    )
    await message.answer("Текст сохранён.")
    return True


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
        if screen == SCREEN_EDIT_MENU:
            utils_state["content_utils_screen"] = SCREEN_VIEW
        else:
            utils_state["content_utils_screen"] = SCREEN_EDIT_MENU
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
    from app.bot.telegram.handlers.admin.keyboards import _utils_inline_keyboard

    await callback.message.answer(
        "🧰 Утилиты админки.\nВыберите подраздел:",
        reply_markup=_utils_inline_keyboard(user_id, codec),
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
    media_items = await store.get_media_items()
    media_count = len(media_items)

    if screen == SCREEN_EDIT_TEXT:
        text = (
            f"{title}\n\n"
            "<b>Редактирование текста</b>\n\n"
            "Отправьте новый текст одним сообщением.\n"
            "Поддерживаются жирный, курсив и подчёркнутый шрифт из Telegram."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, kind, "back"))]
            ]
        )
        return text, keyboard

    if screen == SCREEN_EDIT_MEDIA:
        text = (
            f"{title}\n\n"
            "<b>Добавление медиа</b>\n\n"
            f"Медиа сейчас: {media_count}\n\n"
            "Отправляйте фото, видео или GIF. Когда закончите — нажмите «Готово медиа»."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Готово медиа",
                        callback_data=_encode(codec, user_id, kind, "media_done"),
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, kind, "back"))],
            ]
        )
        return text, keyboard

    if screen == SCREEN_EDIT_MENU:
        preview = body.strip() if body.strip() else "—"
        text = (
            f"{title}\n\n"
            f"<b>Текущий текст:</b>\n{preview}\n\n"
            f"<b>Медиа:</b> {media_count}"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Ред. текст", callback_data=_encode(codec, user_id, kind, "text")),
                    InlineKeyboardButton(text="Доб. медиа", callback_data=_encode(codec, user_id, kind, "media")),
                ],
                [
                    InlineKeyboardButton(
                        text="Очистить медиа",
                        callback_data=_encode(codec, user_id, kind, "clear"),
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, kind, "back"))],
            ]
        )
        return text, keyboard

    preview = body.strip() if body.strip() else "—"
    text = f"{title}\n\n{preview}\n\n<b>Медиа:</b> {media_count}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Редактировать", callback_data=_encode(codec, user_id, kind, "edit"))],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, kind, "back"))],
        ]
    )
    return text, keyboard
