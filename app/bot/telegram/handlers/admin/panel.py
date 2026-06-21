from __future__ import annotations

import asyncio
import logging
import time

from aiogram.types import Message

from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.callback_panel import edit_panel_message
from app.core.container import AppContainer
from app.domain.enums import OrderStatus
from app.services.admin_tools_service import QuestionsAlertStore

from app.bot.telegram.handlers.admin.keyboards import _admin_root_inline_keyboard

logger = logging.getLogger(__name__)

_PANEL_STATS_TTL_SECONDS = 60.0
_panel_stats_cache: tuple[float, tuple[int, dict[str, int], int]] | None = None

ADMIN_PANEL_LOADING_TEXT = (
    "👑 Админ-панель\n"
    "📈 Заказы растут, клиенты ждут!🔥\n"
    "<i>Загрузка статистики…</i>\n"
    "Выберите раздел:"
)


def format_admin_panel_text(
    active_users: int,
    order_counts: dict[str, int],
    unanswered_questions: int,
) -> str:
    new_orders = int(order_counts.get(OrderStatus.PENDING.value, 0))
    return (
        "👑 Админ-панель\n"
        "📈 Заказы растут, клиенты ждут!🔥\n"
        f"👥 Пользователей: {active_users}\n"
        f"📦 Новых заказов: {new_orders}\n"
        f"💬 Неотвеченных вопросов: {unanswered_questions}\n"
        "Выберите раздел:"
    )


def peek_panel_stats_cache() -> tuple[int, dict[str, int], int] | None:
    global _panel_stats_cache
    if _panel_stats_cache is None:
        return None
    if time.monotonic() - _panel_stats_cache[0] >= _PANEL_STATS_TTL_SECONDS:
        return None
    return _panel_stats_cache[1]


async def build_admin_panel_text(container: AppContainer) -> str:
    global _panel_stats_cache
    cached = peek_panel_stats_cache()
    if cached is not None:
        active_users, order_counts, unanswered_questions = cached
    else:
        questions_store = QuestionsAlertStore(container.settings.database.dsn)
        active_users, order_counts, unanswered_questions = await asyncio.gather(
            container.profile_repo.count_active(),
            container.buyout_repo.count_by_status(),
            questions_store.count_unanswered(),
        )
        _panel_stats_cache = (time.monotonic(), (active_users, order_counts, unanswered_questions))
    return format_admin_panel_text(active_users, order_counts, unanswered_questions)


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
) -> Message:
    if text is None:
        text = await build_admin_panel_text(container)
    return await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=_admin_root_inline_keyboard(user_id, callback_codec),
    )


async def refresh_admin_panel_stats(
    panel_message: Message,
    *,
    container: AppContainer,
    user_id: int,
    callback_codec: CallbackCodec,
) -> None:
    try:
        text = await build_admin_panel_text(container)
        await edit_panel_message(
            panel_message,
            text=text,
            reply_markup=_admin_root_inline_keyboard(user_id, callback_codec),
        )
    except Exception:
        logger.exception("Failed to refresh admin panel stats")
