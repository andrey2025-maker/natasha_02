from __future__ import annotations

from aiogram.types import InlineKeyboardButton

from app.domain.enums import OrderStatus

try:
    from aiogram.enums import ButtonStyle as _ButtonStyle
except ImportError:  # aiogram < 3.27
    _ButtonStyle = None

ORDER_FILTER_STATUSES: tuple[OrderStatus, ...] = (
    OrderStatus.PENDING,
    OrderStatus.ISSUED,
    OrderStatus.PICKUP_POINT,
    OrderStatus.IN_TRANSIT,
    OrderStatus.PAID,
    OrderStatus.PAID_CHECK,
    OrderStatus.CANCELLED,
    OrderStatus.PRICE_READY,
    OrderStatus.WAITING_PAYMENT,
)

ORDER_FILTER_EMOJI: dict[OrderStatus, str] = {
    OrderStatus.PENDING: "⏳",
    OrderStatus.ISSUED: "✅",
    OrderStatus.PICKUP_POINT: "📍",
    OrderStatus.IN_TRANSIT: "🚚",
    OrderStatus.PAID: "💳",
    OrderStatus.PAID_CHECK: "🔍",
    OrderStatus.CANCELLED: "❌",
    OrderStatus.PRICE_READY: "💲",
    OrderStatus.WAITING_PAYMENT: "💸",
}

ORDER_FILTER_TITLES: dict[OrderStatus, str] = {
    OrderStatus.PENDING: "Ожидание",
    OrderStatus.ISSUED: "Выданные",
    OrderStatus.PICKUP_POINT: "ПВЗ",
    OrderStatus.IN_TRANSIT: "В пути",
    OrderStatus.PAID: "Оплачен",
    OrderStatus.PAID_CHECK: "Проверка",
    OrderStatus.CANCELLED: "Отменённые",
    OrderStatus.PRICE_READY: "Цена готова",
    OrderStatus.WAITING_PAYMENT: "Ожидает оплату",
}

DEFAULT_ORDER_FILTER_VALUES: list[str] = [status.value for status in ORDER_FILTER_STATUSES]

ORDER_FILTER_BUTTONS_PER_ROW = 2


def order_filter_title(status: OrderStatus) -> str:
    return ORDER_FILTER_TITLES.get(status, status.value)


def order_filter_button_text(status: OrderStatus) -> str:
    emoji = ORDER_FILTER_EMOJI.get(status, "•")
    title = order_filter_title(status)
    return f"{emoji} {title}"


def order_filter_button_style(*, enabled: bool) -> str:
    value = "success" if enabled else "danger"
    if _ButtonStyle is not None:
        return _ButtonStyle.SUCCESS if enabled else _ButtonStyle.DANGER
    return value


def build_order_filter_button(
    status: OrderStatus,
    *,
    enabled: bool,
    callback_data: str,
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=order_filter_button_text(status),
        callback_data=callback_data,
        style=order_filter_button_style(enabled=enabled),
    )
