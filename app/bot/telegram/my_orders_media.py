from __future__ import annotations

from aiogram.types import Message

from app.core.container import AppContainer
from app.domain.models import UserSession
from app.services.admin_tools_service import send_stored_media_to_telegram


async def clear_my_orders_media(bot, chat_id: int, session: UserSession) -> None:
    raw_ids = session.state_data.get("my_orders_media_message_ids")
    if not isinstance(raw_ids, list):
        return
    for raw_id in raw_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=int(raw_id))
        except Exception:
            continue


async def _save_extra_media_ids(
    session: UserSession,
    container: AppContainer,
    extra_ids: list[int],
) -> None:
    state_data = dict(session.state_data)
    state_data["my_orders_media_message_ids"] = extra_ids
    session.state_data = state_data
    await container.session_repo.save(session)


def flatten_order_media_groups(groups: list[tuple[str, list[dict]]]) -> list[dict]:
    result: list[dict] = []
    seen: set[tuple[object, object, object]] = set()
    for _, items in groups:
        for item in items:
            key = (
                item.get("storage_chat_id"),
                item.get("storage_message_id"),
                item.get("file_id"),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
    return result


async def present_my_orders_panel(
    message: Message,
    session: UserSession,
    container: AppContainer,
    *,
    text: str,
    order_media_groups: list[tuple[str, list[dict]]],
    reply_markup,
    replace_message: bool = False,
) -> None:
    await clear_my_orders_media(message.bot, message.chat.id, session)

    if replace_message:
        try:
            await message.delete()
        except Exception:
            pass

    media_items = flatten_order_media_groups(order_media_groups)
    if not media_items:
        await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
        await _save_extra_media_ids(session, container, [])
        return

    bot = message.bot
    chat_id = message.chat.id
    await send_stored_media_to_telegram(
        bot,
        chat_id,
        media_items[0],
        caption=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    extra_ids: list[int] = []
    for media in media_items[1:]:
        sent = await send_stored_media_to_telegram(bot, chat_id, media, caption="")
        if sent and sent.message_id:
            extra_ids.append(int(sent.message_id))
    await _save_extra_media_ids(session, container, extra_ids)
