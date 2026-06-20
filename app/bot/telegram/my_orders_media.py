from __future__ import annotations

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


async def send_my_orders_media(
    bot,
    chat_id: int,
    groups: list[tuple[str, list[dict]]],
) -> list[int]:
    sent_ids: list[int] = []
    for order_number, media_items in groups:
        for index, media in enumerate(media_items):
            caption = f"Выкуп №{order_number}" if index == 0 else None
            sent = await send_stored_media_to_telegram(
                bot,
                chat_id,
                media,
                caption=caption,
                parse_mode="HTML" if caption else None,
            )
            if sent and sent.message_id:
                sent_ids.append(int(sent.message_id))
    return sent_ids


async def sync_my_orders_media(
    bot,
    chat_id: int,
    session: UserSession,
    container: AppContainer,
    *,
    order_media_groups: list[tuple[str, list[dict]]],
    clear_previous: bool = True,
) -> None:
    if clear_previous:
        await clear_my_orders_media(bot, chat_id, session)
    sent_ids = await send_my_orders_media(bot, chat_id, order_media_groups)
    state_data = dict(session.state_data)
    state_data["my_orders_media_message_ids"] = sent_ids
    session.state_data = state_data
    await container.session_repo.save(session)
