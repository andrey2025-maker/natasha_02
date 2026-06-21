from __future__ import annotations

import asyncio
import time

from aiogram.types import Message

from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.callback_panel import edit_panel_message
from app.core.container import AppContainer
from app.domain.enums import OrderStatus
from app.services.admin_tools_service import QuestionsAlertStore

from app.bot.telegram.handlers.admin.keyboards import _admin_root_inline_keyboard

_PANEL_STATS_TTL_SECONDS = 30.0
_panel_stats_cache: tuple[float, tuple[int, dict[str, int], int]] | None = None


async def build_admin_panel_text(container: AppContainer) -> str:
    global _panel_stats_cache
    now = time.monotonic()
    if _panel_stats_cache is not None and now - _panel_stats_cache[0] < _PANEL_STATS_TTL_SECONDS:
        active_users, order_counts, unanswered_questions = _panel_stats_cache[1]
    else:
        questions_store = QuestionsAlertStore(container.settings.database.dsn)
        active_users, order_counts, unanswered_questions = await asyncio.gather(
            container.profile_repo.count_active(),
            container.buyout_repo.count_by_status(),
            questions_store.count_unanswered(),
        )
        _panel_stats_cache = (now, (active_users, order_counts, unanswered_questions))

    new_orders = int(order_counts.get(OrderStatus.PENDING.value, 0))

    return (
        "👑 Админ-панель\n"
        "📈 Заказы растут, клиенты ждут!🔥\n"
        f"👥 Пользователей: {active_users}\n"
        f"📦 Новых заказов: {new_orders}\n"
        f"💬 Неотвеченных вопросов: {unanswered_questions}\n"
        "Выберите раздел:"
    )


async def edit_admin_panel(
    message: Message,
    *,
    container: AppContainer,
    user_id: int,
    callback_codec: CallbackCodec,
) -> None:
    text = await build_admin_panel_text(container)
    await edit_panel_message(
        message,
        text=text,
        reply_markup=_admin_root_inline_keyboard(user_id, callback_codec),
    )


async def send_admin_panel(
    message: Message,
    *,
    container: AppContainer,
    user_id: int,
    callback_codec: CallbackCodec,
    text: str | None = None,
) -> None:
    if text is None:
        text = await build_admin_panel_text(container)
    await message.answer(
        text,
        reply_markup=_admin_root_inline_keyboard(user_id, callback_codec),
    )
