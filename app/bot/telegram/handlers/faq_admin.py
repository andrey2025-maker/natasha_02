from __future__ import annotations

from html import escape

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.callback_panel import edit_panel_message
from app.bot.telegram.message_html import extract_message_html
from app.core.container import AppContainer
from app.services.admin_tools_service import FaqMediaStore

SCREEN_BROWSE = "browse"
SCREEN_EDIT_MENU = "edit_menu"
SCREEN_PICK_TITLE = "pick_title"
SCREEN_PICK_CONTENT = "pick_content"
SCREEN_CONTENT = "content"
SCREEN_ADD_TITLE = "add_title"
SCREEN_RENAME_TITLE = "rename_title"
SCREEN_EDIT_TEXT = "edit_text"
SCREEN_EDIT_MEDIA = "edit_media"
SCREEN_DELETE_CONFIRM = "delete_confirm"

FAQ_ADMIN_STATE_KEYS = (
    "faq_admin_screen",
    "faq_admin_nav_section_id",
    "faq_admin_pick_nav_section_id",
    "faq_admin_target_section_id",
    "faq_admin_panel_chat_id",
    "faq_admin_panel_message_id",
    "awaiting_faq_media_section_id",
)


def faq_admin_has_waiter(utils_state: dict) -> bool:
    screen = str(utils_state.get("faq_admin_screen") or "")
    if screen in {SCREEN_ADD_TITLE, SCREEN_RENAME_TITLE, SCREEN_EDIT_TEXT}:
        return True
    if utils_state.get("awaiting_faq_media_section_id"):
        return True
    return False


def reset_faq_admin_state(utils_state: dict) -> None:
    for key in FAQ_ADMIN_STATE_KEYS:
        utils_state[key] = None


def _parse_section_token(token: str) -> int | None:
    if token == "root":
        return None
    return int(token)


def _section_token(section_id: int | None) -> str:
    return "root" if section_id is None else str(int(section_id))


def _encode(codec: CallbackCodec, user_id: int, suffix: str) -> str:
    return codec.encode(f"admin:faq:{suffix}", user_id)


async def open_faq_admin_panel(
    message: Message,
    *,
    container: AppContainer,
    codec: CallbackCodec,
    user_id: int,
    utils_state: dict,
    faq_media_store: FaqMediaStore,
    edit: bool = False,
) -> None:
    reset_faq_admin_state(utils_state)
    utils_state["faq_admin_screen"] = SCREEN_BROWSE
    utils_state["faq_admin_nav_section_id"] = None
    utils_state["faq_admin_panel_chat_id"] = int(message.chat.id)
    if edit and message.message_id:
        utils_state["faq_admin_panel_message_id"] = int(message.message_id)
    await refresh_faq_admin_panel(
        message=message,
        container=container,
        codec=codec,
        user_id=user_id,
        utils_state=utils_state,
        faq_media_store=faq_media_store,
        force_new=not edit,
    )


async def refresh_faq_admin_panel(
    *,
    message: Message,
    container: AppContainer,
    codec: CallbackCodec,
    user_id: int,
    utils_state: dict,
    faq_media_store: FaqMediaStore,
    force_new: bool = False,
) -> None:
    text, keyboard = await _build_panel(
        container=container,
        codec=codec,
        user_id=user_id,
        utils_state=utils_state,
        faq_media_store=faq_media_store,
    )
    chat_id = int(utils_state.get("faq_admin_panel_chat_id") or message.chat.id)
    panel_message_id = utils_state.get("faq_admin_panel_message_id")
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
            if panel_message_id and int(panel_message_id) == int(message.message_id):
                await edit_panel_message(
                    message,
                    text=text,
                    reply_markup=keyboard,
                )
                return
    sent = await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    utils_state["faq_admin_panel_chat_id"] = int(sent.chat.id)
    utils_state["faq_admin_panel_message_id"] = int(sent.message_id)


async def handle_faq_admin_callback(
    callback: CallbackQuery,
    *,
    action: str,
    container: AppContainer,
    codec: CallbackCodec,
    faq_media_store: FaqMediaStore,
    utils_state: dict,
) -> bool:
    if not callback.from_user or not callback.message:
        return False
    if not action.startswith("admin:faq:"):
        return False

    user_id = callback.from_user.id
    suffix = action[len("admin:faq:") :]

    if suffix.startswith("nav:"):
        section_id = _parse_section_token(suffix.split(":", maxsplit=1)[1])
        utils_state["faq_admin_screen"] = SCREEN_BROWSE
        utils_state["faq_admin_nav_section_id"] = section_id
        utils_state["awaiting_faq_media_section_id"] = None
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix == "add":
        utils_state["faq_admin_screen"] = SCREEN_ADD_TITLE
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix == "edit":
        utils_state["faq_admin_screen"] = SCREEN_EDIT_MENU
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix == "pick:title":
        utils_state["faq_admin_screen"] = SCREEN_PICK_TITLE
        utils_state["faq_admin_pick_nav_section_id"] = utils_state.get("faq_admin_nav_section_id")
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix == "pick:content":
        utils_state["faq_admin_screen"] = SCREEN_PICK_CONTENT
        utils_state["faq_admin_pick_nav_section_id"] = utils_state.get("faq_admin_nav_section_id")
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix.startswith("pick:title:nav:"):
        section_id = _parse_section_token(suffix.split(":", maxsplit=3)[3])
        utils_state["faq_admin_screen"] = SCREEN_PICK_TITLE
        utils_state["faq_admin_pick_nav_section_id"] = section_id
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix.startswith("pick:content:nav:"):
        section_id = _parse_section_token(suffix.split(":", maxsplit=3)[3])
        utils_state["faq_admin_screen"] = SCREEN_PICK_CONTENT
        utils_state["faq_admin_pick_nav_section_id"] = section_id
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix.startswith("pick:title:select:"):
        section_id = int(suffix.split(":", maxsplit=3)[3])
        utils_state["faq_admin_screen"] = SCREEN_RENAME_TITLE
        utils_state["faq_admin_target_section_id"] = section_id
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix.startswith("pick:content:select:"):
        section_id = int(suffix.split(":", maxsplit=3)[3])
        utils_state["faq_admin_screen"] = SCREEN_CONTENT
        utils_state["faq_admin_target_section_id"] = section_id
        utils_state["awaiting_faq_media_section_id"] = None
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix.startswith("content:text:"):
        section_id = int(suffix.split(":", maxsplit=2)[2])
        utils_state["faq_admin_screen"] = SCREEN_EDIT_TEXT
        utils_state["faq_admin_target_section_id"] = section_id
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix.startswith("content:media:"):
        section_id = int(suffix.split(":", maxsplit=2)[2])
        utils_state["faq_admin_screen"] = SCREEN_EDIT_MEDIA
        utils_state["faq_admin_target_section_id"] = section_id
        utils_state["awaiting_faq_media_section_id"] = section_id
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix.startswith("content:media_done:"):
        section_id = int(suffix.split(":", maxsplit=2)[2])
        utils_state["faq_admin_screen"] = SCREEN_CONTENT
        utils_state["faq_admin_target_section_id"] = section_id
        utils_state["awaiting_faq_media_section_id"] = None
        await callback.answer("Сохранено")
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix.startswith("content:clear:"):
        section_id = int(suffix.split(":", maxsplit=2)[2])
        await faq_media_store.clear_media(section_id)
        utils_state["faq_admin_screen"] = SCREEN_CONTENT
        utils_state["faq_admin_target_section_id"] = section_id
        await callback.answer("Медиа очищено")
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix.startswith("content:delete:yes:"):
        section_id = int(suffix.split(":", maxsplit=3)[3])
        section = await container.faq_service.get_section(section_id)
        if not section:
            await callback.answer("Раздел не найден", show_alert=True)
            return True
        parent_id = section.parent_id
        tree_ids = await container.faq_service.delete_section(section_id)
        if not tree_ids:
            await callback.answer("Раздел не найден", show_alert=True)
            return True
        for item_id in tree_ids:
            await faq_media_store.clear_media(item_id)
        utils_state["faq_admin_screen"] = SCREEN_BROWSE
        utils_state["faq_admin_nav_section_id"] = parent_id
        utils_state["faq_admin_target_section_id"] = None
        utils_state["awaiting_faq_media_section_id"] = None
        await callback.answer("Раздел удалён")
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix.startswith("content:delete:"):
        section_id = int(suffix.split(":", maxsplit=2)[2])
        utils_state["faq_admin_screen"] = SCREEN_DELETE_CONFIRM
        utils_state["faq_admin_target_section_id"] = section_id
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix == "cancel":
        utils_state["faq_admin_screen"] = SCREEN_BROWSE
        utils_state["faq_admin_target_section_id"] = None
        utils_state["awaiting_faq_media_section_id"] = None
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return True

    if suffix == "back":
        await _handle_back(
            callback,
            container=container,
            codec=codec,
            faq_media_store=faq_media_store,
            utils_state=utils_state,
        )
        return True

    return False


async def try_handle_faq_admin_text(
    message: Message,
    *,
    container: AppContainer,
    codec: CallbackCodec,
    faq_media_store: FaqMediaStore,
    utils_state: dict,
) -> bool:
    if not message.from_user:
        return False
    screen = str(utils_state.get("faq_admin_screen") or "")
    user_id = message.from_user.id

    if screen == SCREEN_ADD_TITLE:
        if not message.text:
            return False
        text = message.text.strip()
        if not text:
            await message.answer("Текст не может быть пустым.")
            return True
        parent_id = utils_state.get("faq_admin_nav_section_id")
        if parent_id is not None:
            parent_id = int(parent_id)
        created = await container.faq_service.create_section(parent_id=parent_id, title=text)
        utils_state["faq_admin_screen"] = SCREEN_BROWSE
        utils_state["faq_admin_nav_section_id"] = parent_id
        await refresh_faq_admin_panel(
            message=message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        await message.answer(f"Раздел «{escape(created.title, quote=False)}» добавлен.", parse_mode="HTML")
        return True

    if screen == SCREEN_RENAME_TITLE:
        if not message.text:
            return False
        text = message.text.strip()
        if not text:
            await message.answer("Текст не может быть пустым.")
            return True
        section_id = int(utils_state.get("faq_admin_target_section_id") or 0)
        updated = await container.faq_service.update_section_title(section_id, text)
        if not updated:
            await message.answer("Раздел не найден.")
            return True
        utils_state["faq_admin_screen"] = SCREEN_BROWSE
        utils_state["faq_admin_nav_section_id"] = updated.parent_id
        utils_state["faq_admin_target_section_id"] = None
        await refresh_faq_admin_panel(
            message=message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        await message.answer("Название кнопки обновлено.")
        return True

    if screen == SCREEN_EDIT_TEXT:
        if not message.text:
            return False
        section_id = int(utils_state.get("faq_admin_target_section_id") or 0)
        html_text = extract_message_html(message)
        if not html_text.strip():
            await message.answer("Текст не может быть пустым.")
            return True
        updated = await container.faq_service.update_section_text(section_id, html_text)
        if not updated:
            await message.answer("Раздел не найден.")
            return True
        utils_state["faq_admin_screen"] = SCREEN_CONTENT
        await refresh_faq_admin_panel(
            message=message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        await message.answer("Текст раздела обновлён.")
        return True

    return False


async def _handle_back(
    callback: CallbackQuery,
    *,
    container: AppContainer,
    codec: CallbackCodec,
    faq_media_store: FaqMediaStore,
    utils_state: dict,
) -> None:
    screen = str(utils_state.get("faq_admin_screen") or SCREEN_BROWSE)
    user_id = callback.from_user.id

    if screen == SCREEN_BROWSE:
        await callback.answer()
        return

    if screen == SCREEN_EDIT_MENU:
        utils_state["faq_admin_screen"] = SCREEN_BROWSE
        utils_state["awaiting_faq_media_section_id"] = None
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return

    if screen in {SCREEN_PICK_TITLE, SCREEN_PICK_CONTENT}:
        pick_nav = utils_state.get("faq_admin_pick_nav_section_id")
        if pick_nav is None:
            utils_state["faq_admin_screen"] = SCREEN_EDIT_MENU
            await callback.answer()
            await refresh_faq_admin_panel(
                message=callback.message,
                container=container,
                codec=codec,
                user_id=user_id,
                utils_state=utils_state,
                faq_media_store=faq_media_store,
            )
            return
        section = await container.faq_service.get_section(int(pick_nav))
        utils_state["faq_admin_pick_nav_section_id"] = section.parent_id if section else None
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return

    if screen in {SCREEN_CONTENT, SCREEN_DELETE_CONFIRM, SCREEN_EDIT_MEDIA}:
        utils_state["faq_admin_screen"] = SCREEN_PICK_CONTENT
        utils_state["faq_admin_target_section_id"] = None
        utils_state["awaiting_faq_media_section_id"] = None
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return

    if screen in {SCREEN_ADD_TITLE, SCREEN_RENAME_TITLE, SCREEN_EDIT_TEXT}:
        if screen == SCREEN_ADD_TITLE:
            utils_state["faq_admin_screen"] = SCREEN_BROWSE
        elif screen == SCREEN_RENAME_TITLE:
            utils_state["faq_admin_screen"] = SCREEN_PICK_TITLE
        else:
            utils_state["faq_admin_screen"] = SCREEN_CONTENT
        await callback.answer()
        await refresh_faq_admin_panel(
            message=callback.message,
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            faq_media_store=faq_media_store,
        )
        return

    utils_state["faq_admin_screen"] = SCREEN_BROWSE
    await callback.answer()
    await refresh_faq_admin_panel(
        message=callback.message,
        container=container,
        codec=codec,
        user_id=user_id,
        utils_state=utils_state,
        faq_media_store=faq_media_store,
    )


async def _build_panel(
    *,
    container: AppContainer,
    codec: CallbackCodec,
    user_id: int,
    utils_state: dict,
    faq_media_store: FaqMediaStore,
) -> tuple[str, InlineKeyboardMarkup]:
    screen = str(utils_state.get("faq_admin_screen") or SCREEN_BROWSE)

    if screen == SCREEN_EDIT_MENU:
        return _edit_menu_panel(codec, user_id)

    if screen == SCREEN_ADD_TITLE:
        nav_id = utils_state.get("faq_admin_nav_section_id")
        path = await container.faq_service.breadcrumbs(int(nav_id) if nav_id is not None else None)
        text = (
            f"📚 <b>Управление FAQ</b>\n"
            f"{escape(path, quote=False)}\n\n"
            "Введите название нового подраздела одним сообщением."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, "back"))],
            ]
        )
        return text, keyboard

    if screen == SCREEN_RENAME_TITLE:
        section_id = int(utils_state.get("faq_admin_target_section_id") or 0)
        section = await container.faq_service.get_section(section_id)
        title = section.title if section else "?"
        text = (
            "📚 <b>Редактирование кнопки</b>\n\n"
            f"Раздел: <b>{escape(title, quote=False)}</b>\n\n"
            "Отправьте новое название кнопки одним сообщением."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, "back"))],
            ]
        )
        return text, keyboard

    if screen == SCREEN_EDIT_TEXT:
        section_id = int(utils_state.get("faq_admin_target_section_id") or 0)
        section = await container.faq_service.get_section(section_id)
        title = section.title if section else "?"
        text = (
            "📚 <b>Редактирование текста</b>\n\n"
            f"Раздел: <b>{escape(title, quote=False)}</b>\n\n"
            "Отправьте новый текст одним сообщением.\n"
            "Поддерживается форматирование: <b>жирный</b>, <i>курсив</i>, ссылки и т.д."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, "back"))],
            ]
        )
        return text, keyboard

    if screen == SCREEN_EDIT_MEDIA:
        section_id = int(utils_state.get("faq_admin_target_section_id") or 0)
        section = await container.faq_service.get_section(section_id)
        media_count = len(await faq_media_store.get_media_items(section_id))
        title = section.title if section else "?"
        text = (
            "📚 <b>Добавление медиа</b>\n\n"
            f"Раздел: <b>{escape(title, quote=False)}</b>\n"
            f"Медиа сейчас: {media_count}\n\n"
            "Отправляйте фото, видео или GIF. Когда закончите — нажмите «Готово медиа»."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Готово медиа",
                        callback_data=_encode(codec, user_id, f"content:media_done:{section_id}"),
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, "back"))],
            ]
        )
        return text, keyboard

    if screen == SCREEN_DELETE_CONFIRM:
        section_id = int(utils_state.get("faq_admin_target_section_id") or 0)
        section = await container.faq_service.get_section(section_id)
        title = section.title if section else "?"
        text = (
            "⚠️ <b>Удалить раздел?</b>\n\n"
            f"«{escape(title, quote=False)}» и все вложенные подразделы будут удалены без восстановления."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Да, удалить",
                        callback_data=_encode(codec, user_id, f"content:delete:yes:{section_id}"),
                    ),
                    InlineKeyboardButton(text="Отмена", callback_data=_encode(codec, user_id, "cancel")),
                ]
            ]
        )
        return text, keyboard

    if screen == SCREEN_CONTENT:
        section_id = int(utils_state.get("faq_admin_target_section_id") or 0)
        section = await container.faq_service.get_section(section_id)
        if not section:
            text = "Раздел не найден."
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, "back"))]
                ]
            )
            return text, keyboard
        path = await container.faq_service.breadcrumbs(section_id)
        media_items = await faq_media_store.get_media_items(section_id)
        body = section.content_text.strip() if section.content_text else "—"
        text = (
            f"📚 <b>Контент раздела</b>\n"
            f"{escape(path, quote=False)}\n\n"
            f"<b>Текст:</b>\n{escape(body, quote=False)}\n\n"
            f"<b>Медиа:</b> {len(media_items)}"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Ред. текст",
                        callback_data=_encode(codec, user_id, f"content:text:{section_id}"),
                    ),
                    InlineKeyboardButton(
                        text="Доб. медиа",
                        callback_data=_encode(codec, user_id, f"content:media:{section_id}"),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Очистить медиа",
                        callback_data=_encode(codec, user_id, f"content:clear:{section_id}"),
                    ),
                    InlineKeyboardButton(
                        text="Удалить",
                        callback_data=_encode(codec, user_id, f"content:delete:{section_id}"),
                    ),
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, "back"))],
            ]
        )
        return text, keyboard

    if screen == SCREEN_PICK_TITLE:
        return await _build_pick_panel(
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            pick_kind="title",
        )

    if screen == SCREEN_PICK_CONTENT:
        return await _build_pick_panel(
            container=container,
            codec=codec,
            user_id=user_id,
            utils_state=utils_state,
            pick_kind="content",
        )

    return await _build_browse_panel(
        container=container,
        codec=codec,
        user_id=user_id,
        nav_section_id=utils_state.get("faq_admin_nav_section_id"),
    )


async def _build_browse_panel(
    *,
    container: AppContainer,
    codec: CallbackCodec,
    user_id: int,
    nav_section_id: int | None,
) -> tuple[str, InlineKeyboardMarkup]:
    if nav_section_id is not None:
        nav_section_id = int(nav_section_id)
    path = await container.faq_service.breadcrumbs(nav_section_id)
    children = await container.faq_service.list_children(nav_section_id)
    lines = [
        "📚 <b>Управление FAQ</b>",
        escape(path, quote=False),
        "",
        "Подразделы:" if children else "Подразделов пока нет.",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for child in children:
        rows.append(
            [
                InlineKeyboardButton(
                    text=child.title,
                    callback_data=_encode(codec, user_id, f"nav:{child.id}"),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="Добавить", callback_data=_encode(codec, user_id, "add")),
            InlineKeyboardButton(text="Ред.", callback_data=_encode(codec, user_id, "edit")),
        ]
    )
    if nav_section_id is not None:
        section = await container.faq_service.get_section(nav_section_id)
        parent_token = _section_token(section.parent_id if section else None)
        rows.append(
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, f"nav:{parent_token}"))]
        )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _edit_menu_panel(codec: CallbackCodec, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    text = "📚 <b>Редактирование FAQ</b>\n\nВыберите, что редактировать:"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Разделы", callback_data=_encode(codec, user_id, "pick:title")),
                InlineKeyboardButton(text="Текст", callback_data=_encode(codec, user_id, "pick:content")),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, "back"))],
        ]
    )
    return text, keyboard


async def _build_pick_panel(
    *,
    container: AppContainer,
    codec: CallbackCodec,
    user_id: int,
    utils_state: dict,
    pick_kind: str,
) -> tuple[str, InlineKeyboardMarkup]:
    pick_nav = utils_state.get("faq_admin_pick_nav_section_id")
    if pick_nav is not None:
        pick_nav = int(pick_nav)
    path = await container.faq_service.breadcrumbs(pick_nav)
    children = await container.faq_service.list_children(pick_nav)
    kind_title = "кнопки" if pick_kind == "title" else "контента"
    lines = [
        f"📚 <b>Выбор раздела для {kind_title}</b>",
        escape(path, quote=False),
        "",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    if pick_nav is not None:
        section = await container.faq_service.get_section(pick_nav)
        if section:
            select_action = f"pick:{pick_kind}:select:{section.id}"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"✅ {section.title}",
                        callback_data=_encode(codec, user_id, select_action),
                    )
                ]
            )
            lines.append(f"Текущий: {section.title}")
    if children:
        lines.append("Или откройте подраздел:")
    else:
        lines.append("Вложенных подразделов нет.")
    for child in children:
        rows.append(
            [
                InlineKeyboardButton(
                    text=child.title,
                    callback_data=_encode(codec, user_id, f"pick:{pick_kind}:nav:{child.id}"),
                )
            ]
        )
    if pick_nav is not None:
        current = await container.faq_service.get_section(pick_nav)
        back_token = _section_token(current.parent_id if current else None)
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=_encode(codec, user_id, f"pick:{pick_kind}:nav:{back_token}"),
                )
            ]
        )
    else:
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=_encode(codec, user_id, "back"))])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)
