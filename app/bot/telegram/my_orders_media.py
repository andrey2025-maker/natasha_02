from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.types import Message

from app.bot.telegram.callback_panel import edit_panel_message
from app.core.container import AppContainer
from app.domain.models import UserSession
from app.services.admin_tools_service import (
    clip_html_caption,
    send_stored_media_group_to_telegram,
)

logger = logging.getLogger(__name__)

MY_ORDERS_LOADING_TEXT = "<b>Мои заказы</b>\n\n<i>Загрузка…</i>"


async def _delete_message_ids(bot: Bot, chat_id: int, raw_ids: object) -> None:
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


async def clear_my_orders_media(bot: Bot, chat_id: int, session: UserSession) -> None:
    await _delete_message_ids(bot, chat_id, session.state_data.get("my_orders_media_message_ids"))


async def _clear_my_orders_panel(bot: Bot, chat_id: int, session: UserSession) -> None:
    panel_id = session.state_data.get("my_orders_panel_message_id")
    if panel_id is not None:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=int(panel_id))
        except Exception:
            pass


async def clear_my_orders_panel(bot: Bot, chat_id: int, session: UserSession) -> None:
    await clear_my_orders_media(bot, chat_id, session)
    await _clear_my_orders_panel(bot, chat_id, session)
    state_data = dict(session.state_data)
    state_data["my_orders_media_message_ids"] = []
    state_data.pop("my_orders_panel_message_id", None)
    session.state_data = state_data


async def _save_my_orders_panel_state(
    session: UserSession,
    container: AppContainer,
    *,
    media_ids: list[int],
    panel_message_id: int,
) -> None:
    state_data = dict(session.state_data)
    state_data["my_orders_media_message_ids"] = list(media_ids)
    state_data["my_orders_panel_message_id"] = int(panel_message_id)
    session.state_data = state_data
    await container.session_repo.save(session)


async def send_my_orders_media(
    bot: Bot,
    chat_id: int,
    order_media_groups: list[tuple[str, list[dict]]],
) -> list[int]:
    media_items = flatten_order_media_groups(order_media_groups)
    if not media_items:
        return []
    ids = await send_stored_media_group_to_telegram(
        bot,
        chat_id,
        media_items,
        caption=None,
        parse_mode=None,
        reply_markup=None,
    )
    return list(ids) if ids else []


async def deliver_my_orders_panel(
    bot: Bot,
    chat_id: int,
    session: UserSession,
    container: AppContainer,
    *,
    text: str,
    order_media_groups: list[tuple[str, list[dict]]],
    reply_markup,
) -> Message:
    """Удаляет старые медиа и панель, отправляет медиа сверху, текст с кнопками снизу."""
    await clear_my_orders_panel(bot, chat_id, session)

    media_ids = await send_my_orders_media(bot, chat_id, order_media_groups)
    panel_message = await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    await _save_my_orders_panel_state(
        session,
        container,
        media_ids=media_ids,
        panel_message_id=int(panel_message.message_id),
    )
    return panel_message


async def _deliver_orders_panel(
    message: Message,
    *,
    text: str,
    media_items: list[dict],
    reply_markup,
    replace_message: bool,
) -> list[int]:
    """Admin orders panel: media above, full text + keyboard below."""
    bot = message.bot
    chat_id = message.chat.id

    if replace_message:
        try:
            await message.delete()
        except Exception:
            pass

    extra_ids: list[int] = []
    if media_items:
        ids = await send_stored_media_group_to_telegram(
            bot,
            chat_id,
            media_items,
            caption=None,
            parse_mode=None,
            reply_markup=None,
        )
        extra_ids = list(ids) if ids else []

    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    return extra_ids


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
    loading_message: Message | None = None,
) -> Message:
    bot = message.bot
    chat_id = int(message.chat.id)

    if loading_message is None:
        loading_message = await bot.send_message(
            chat_id=chat_id,
            text=MY_ORDERS_LOADING_TEXT,
            parse_mode="HTML",
        )

    async def work() -> None:
        try:
            if replace_message:
                await clear_my_orders_media(bot, chat_id, session)
                try:
                    await message.delete()
                except Exception:
                    pass
                state_data = dict(session.state_data)
                state_data.pop("my_orders_panel_message_id", None)
                session.state_data = state_data

            await buyout_flow.prepare_preferences(session, persist=True)
            detailed = await buyout_flow.render_orders(
                session,
                page=page,
                include_details=True,
                profile=profile,
            )
            detailed_markup = build_reply_markup(user_id, session, detailed)
            try:
                await loading_message.delete()
            except Exception:
                pass
            await deliver_my_orders_panel(
                bot,
                chat_id,
                session,
                container,
                text=detailed.text,
                order_media_groups=detailed.order_media_groups,
                reply_markup=detailed_markup,
            )
        except Exception:
            logger.exception("Failed to load my orders panel")
            try:
                fast = await buyout_flow.render_orders(
                    session,
                    page=page,
                    include_details=False,
                    profile=profile,
                )
                fallback_markup = build_reply_markup(user_id, session, fast)
                await edit_panel_message(
                    loading_message,
                    text=fast.text,
                    reply_markup=fallback_markup,
                )
            except Exception:
                pass

    asyncio.create_task(work())
    return loading_message


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
    bot = message.bot
    chat_id = int(message.chat.id)
    if replace_message:
        await clear_my_orders_media(bot, chat_id, session)
        try:
            await message.delete()
        except Exception:
            pass
    await deliver_my_orders_panel(
        bot,
        chat_id,
        session,
        container,
        text=text,
        order_media_groups=order_media_groups,
        reply_markup=reply_markup,
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
