from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from app.services.admin_tools_service import send_content_with_media_to_telegram


def message_has_media(message: Message) -> bool:
    return bool(message.photo or message.video or message.animation or message.document)


async def edit_panel_message(
    message: Message,
    *,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> None:
    try:
        if message_has_media(message):
            await message.edit_caption(
                caption=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        else:
            await message.edit_text(
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        return
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if "message is not modified" in error_text:
            return
        if "can't edit" in error_text or "message to edit not found" in error_text:
            pass
        elif message_has_media(message):
            pass
        else:
            raise

    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)


async def edit_content_with_media(
    message: Message,
    *,
    text: str,
    media_items: list[dict],
    reply_markup=None,
    parse_mode: str = "HTML",
) -> None:
    if not media_items:
        await edit_panel_message(
            message,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        return

    if message_has_media(message):
        try:
            await message.edit_caption(
                caption=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest as exc:
            error_text = str(exc).lower()
            if "message is not modified" in error_text:
                return

    try:
        await message.delete()
    except Exception:
        pass
    await send_content_with_media_to_telegram(
        message,
        text=text,
        media_items=media_items,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )
