from __future__ import annotations

import asyncio
import logging

from aiogram.types import Message

from app.bot.telegram.callback_panel import edit_panel_message
from app.core.container import AppContainer
from app.domain.models import UserSession
from app.services.admin_tools_service import (
    clip_html_caption,
    send_stored_media_group_to_telegram,
)

logger = logging.getLogger(__name__)


async def _delete_message_ids(bot, chat_id: int, raw_ids: object) -> None:
    if not isinstance(raw_ids, list):
        return
    for raw_id in raw_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=int(raw_id))
        except Exception:
            continue


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


async def _ensure_reply_markup(bot, chat_id: int, message_id: int, reply_markup) -> None:
    if not reply_markup:
        return
    for attempt in range(3):
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
            return
        except Exception:
            if attempt < 2:
                await asyncio.sleep(0.15 * (attempt + 1))
            else:
                logger.exception(
                    "Failed to attach my orders reply_markup (chat_id=%s message_id=%s)",
                    chat_id,
                    message_id,
                )


async def _deliver_orders_panel(
    message: Message,
    *,
    text: str,
    media_items: list[dict],
    reply_markup,
    replace_message: bool,
) -> list[int]:
    if replace_message:
        try:
            await message.delete()
        except Exception:
            pass

    if not media_items:
        await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
        return []

    bot = message.bot
    chat_id = message.chat.id
    caption = clip_html_caption(text)
    all_ids = await send_stored_media_group_to_telegram(
        bot,
        chat_id,
        media_items,
        caption=caption,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    if not all_ids:
        await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
        return []
    await _ensure_reply_markup(bot, chat_id, all_ids[0], reply_markup)
    return all_ids[1:]


async def clear_my_orders_media(bot, chat_id: int, session: UserSession) -> None:
    await _delete_message_ids(bot, chat_id, session.state_data.get("my_orders_media_message_ids"))


async def _save_my_orders_extra_media_ids(
    session: UserSession,
    container: AppContainer,
    extra_ids: list[int],
) -> None:
    state_data = dict(session.state_data)
    state_data["my_orders_media_message_ids"] = extra_ids
    session.state_data = state_data
    await container.session_repo.save(session)


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
    media_items = flatten_order_media_groups(order_media_groups)
    if not media_items:
        if replace_message:
            await edit_panel_message(message, text=text, reply_markup=reply_markup)
            extra_ids: list[int] = []
        else:
            await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
            extra_ids = []
    else:
        extra_ids = await _deliver_orders_panel(
            message,
            text=text,
            media_items=media_items,
            reply_markup=reply_markup,
            replace_message=replace_message,
        )
    await _save_my_orders_extra_media_ids(session, container, extra_ids)


async def open_my_orders_panel(
    message: Message,
    session: UserSession,
    container: AppContainer,
    buyout_flow,
    *,
    page: int,
    user_id: int,
    build_reply_markup,
    replace_message: bool = False,
    profile=None,
) -> None:
    await buyout_flow.prepare_preferences(session, persist=True)
    response = await buyout_flow.render_orders(
        session,
        page=page,
        include_details=True,
        profile=profile,
    )
    markup = await build_reply_markup(user_id, session, response)
    await present_my_orders_panel(
        message,
        session,
        container,
        text=response.text,
        order_media_groups=response.order_media_groups,
        reply_markup=markup,
        replace_message=replace_message,
    )


async def clear_admin_orders_extra_media(bot, chat_id: int, state: dict) -> None:
    await _delete_message_ids(bot, chat_id, state.get("extra_media_message_ids"))


async def present_admin_orders_panel(
    message: Message,
    state: dict,
    *,
    text: str,
    order_media_groups: list[tuple[str, list[dict]]],
    reply_markup,
    replace_message: bool = False,
) -> list[int]:
    await clear_admin_orders_extra_media(message.bot, message.chat.id, state)
    return await _deliver_orders_panel(
        message,
        text=text,
        media_items=flatten_order_media_groups(order_media_groups),
        reply_markup=reply_markup,
        replace_message=replace_message,
    )
