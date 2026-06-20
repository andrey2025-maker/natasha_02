from __future__ import annotations

from aiogram.types import Message

from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.callback_panel import edit_panel_message
from app.core.container import AppContainer
from app.domain.enums import OrderStatus
from app.services.admin_tools_service import QuestionsAlertStore

from app.bot.telegram.handlers.admin.keyboards import _admin_root_inline_keyboard


async def build_admin_panel_text(container: AppContainer) -> str:
    active_users = await container.profile_repo.count_active()
    order_counts = await container.buyout_repo.count_by_status()
    new_orders = int(order_counts.get(OrderStatus.PENDING.value, 0))
    questions_store = QuestionsAlertStore(container.settings.database.dsn)
    unanswered_questions = await questions_store.count_unanswered()

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
) -> None:
    text = await build_admin_panel_text(container)
    await message.answer(
        text,
        reply_markup=_admin_root_inline_keyboard(user_id, callback_codec),
    )
