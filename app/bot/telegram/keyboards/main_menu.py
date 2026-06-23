from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.domain.enums import OrderStatus

from app.domain.models import UserProfile

from app.bot.telegram.callbacks import CallbackCodec
from app.services.order_filter_config import (
    ORDER_FILTER_BUTTONS_PER_ROW,
    ORDER_FILTER_STATUSES,
    order_filter_button_text,
    order_filter_title,
)


def main_menu_keyboard(include_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="❓ Вопросы")],
        [KeyboardButton(text="🚫 Запрещенные товары"), KeyboardButton(text="☎️ Наши контакты")],
    ]
    if include_admin:
        rows.append([KeyboardButton(text="🛠 Админ")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def profile_menu_keyboard(
    other_platform_label: str,
    user_id: int,
    codec: CallbackCodec,
    profile: UserProfile | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if profile and profile.is_filled:
        rows.append(
            [InlineKeyboardButton(text="📦 Трек номер", callback_data=codec.encode("profile:track:open", user_id))]
        )
    if not (profile and profile.is_filled):
        rows.append(
            [InlineKeyboardButton(text="📝 Заполнить профиль", callback_data=codec.encode("profile:start_fill", user_id))]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔗 Есть профиль {other_platform_label}",
                    callback_data=codec.encode("profile:start_sync", user_id),
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="🛍 Заказ выкупа", callback_data=codec.encode("profile:buyout_start", user_id))],
            [InlineKeyboardButton(text="📦 Мои заказы", callback_data=codec.encode("profile:buyout_orders", user_id))],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def track_mode_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔢 Только трек номера",
                    callback_data=codec.encode("profile:track:mode:numbers", user_id),
                ),
                InlineKeyboardButton(
                    text="💬 С комментариями",
                    callback_data=codec.encode("profile:track:mode:comments", user_id),
                ),
            ],
        ]
    )


def track_continue_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Ещё",
                    callback_data=codec.encode("profile:track:more", user_id),
                ),
                InlineKeyboardButton(
                    text="📋 Все",
                    callback_data=codec.encode("profile:track:done", user_id),
                ),
            ],
        ]
    )


def profile_confirm_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data=codec.encode("confirm_yes", user_id))],
            [
                InlineKeyboardButton(text="👤 Имя", callback_data=codec.encode("edit_name", user_id)),
                InlineKeyboardButton(text="📞 Тел.", callback_data=codec.encode("edit_phone", user_id)),
                InlineKeyboardButton(text="🏙 Город", callback_data=codec.encode("edit_city", user_id)),
            ],
        ]
    )


def yes_no_keyboard(yes_action: str, no_action: str, user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=codec.encode(yes_action, user_id)),
                InlineKeyboardButton(text="❌ Нет", callback_data=codec.encode(no_action, user_id)),
            ]
        ]
    )


def platforms_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛍 Заказ выкупа", callback_data=codec.encode("profile:buyout_start", user_id))],
            [InlineKeyboardButton(text="📦 Мои заказы", callback_data=codec.encode("profile:buyout_orders", user_id))],
            [InlineKeyboardButton(text="🎛 Фильтры заказов", callback_data=codec.encode("profile:buyout_filters", user_id))],
        ]
    )


def my_orders_pagination_keyboard(
    user_id: int,
    current_page: int,
    total_pages: int,
    codec: CallbackCodec,
) -> InlineKeyboardMarkup | None:
    if total_pages <= 1:
        return None
    buttons: list[InlineKeyboardButton] = []
    if current_page > 1:
        buttons.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=codec.encode(f"my_orders:{current_page - 1}", user_id),
            )
        )
    if current_page < total_pages:
        buttons.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=codec.encode(f"my_orders:{current_page + 1}", user_id),
            )
        )
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def my_orders_filters_keyboard(
    user_id: int,
    filters: dict[OrderStatus, bool],
    codec: CallbackCodec,
) -> InlineKeyboardMarkup:
    return _my_orders_filters_rows(user_id, filters, codec)


def my_orders_message_keyboard(
    *,
    user_id: int,
    current_page: int,
    total_pages: int,
    filters: dict[OrderStatus, bool],
    codec: CallbackCodec,
) -> InlineKeyboardMarkup:
    rows = _my_orders_filters_rows(user_id, filters, codec).inline_keyboard
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if current_page > 1:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=codec.encode(f"my_orders:{current_page - 1}", user_id),
                )
            )
        if current_page < total_pages:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=codec.encode(f"my_orders:{current_page + 1}", user_id),
                )
            )
        if nav:
            rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _my_orders_filters_rows(
    user_id: int,
    filters: dict[OrderStatus, bool],
    codec: CallbackCodec,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for status in ORDER_FILTER_STATUSES:
        is_enabled = filters.get(status, True)
        row.append(
            InlineKeyboardButton(
                text=order_filter_button_text(status, enabled=is_enabled),
                callback_data=codec.encode(f"orders_filter:{status.value}", user_id),
            )
        )
        if len(row) == ORDER_FILTER_BUTTONS_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _my_orders_filter_title(status: OrderStatus) -> str:
    return order_filter_title(status)


def admin_menu_keyboard(is_main_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="Профили"), KeyboardButton(text="Блокировки")],
        [KeyboardButton(text="Заказы"), KeyboardButton(text="Статистика")],
        [KeyboardButton(text="Вопросы"), KeyboardButton(text="Контент")],
        [KeyboardButton(text="Рассылка"), KeyboardButton(text="Утилиты")],
        [KeyboardButton(text="Список админов")],
    ]
    if is_main_admin:
        rows.append([KeyboardButton(text="Добавить админа"), KeyboardButton(text="Удалить админа")])
    rows.append([KeyboardButton(text="Назад")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def buyout_add_more_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Ещё товар"), KeyboardButton(text="Нет")],
        ],
        resize_keyboard=True,
    )


def buyout_add_more_inline_keyboard(*, user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Ещё товар",
                    callback_data=codec.encode("buyout:add:more", user_id),
                ),
                InlineKeyboardButton(
                    text="✅ Нет",
                    callback_data=codec.encode("buyout:add:done", user_id),
                ),
            ]
        ]
    )
