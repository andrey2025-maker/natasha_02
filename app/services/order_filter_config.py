from __future__ import annotations

from app.domain.enums import OrderStatus

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


def order_filter_button_text(status: OrderStatus, *, enabled: bool) -> str:
    indicator = "🟢" if enabled else "🔴"
    emoji = ORDER_FILTER_EMOJI.get(status, "•")
    title = order_filter_title(status)
    return f"{indicator} {emoji} {title}"
