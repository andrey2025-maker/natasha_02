from __future__ import annotations

from html import escape

from app.domain.enums import OrderStatus
from app.domain.models import BuyoutOrder, OrderStatusHistoryItem


def order_status_title(status: OrderStatus) -> str:
    titles = {
        OrderStatus.PENDING: "Ожидание",
        OrderStatus.PRICE_READY: "Цена готова",
        OrderStatus.WAITING_PAYMENT: "Ожидает оплату",
        OrderStatus.PAID_CHECK: "Проверка оплаты",
        OrderStatus.PAID: "Оплачен",
        OrderStatus.IN_TRANSIT: "В пути",
        OrderStatus.PICKUP_POINT: "В пункте выдачи",
        OrderStatus.ISSUED: "Выдан",
        OrderStatus.CANCELLED: "Отменен",
    }
    return titles.get(status, status.value)


def format_order_history_short(items: list[OrderStatusHistoryItem]) -> str:
    if not items:
        return "История: —"
    lines = ["История:"]
    for item in items:
        prev = order_status_title(item.previous_status) if item.previous_status else "—"
        lines.append(
            f"- {item.changed_at.strftime('%d.%m.%y')} {prev} → {order_status_title(item.new_status)}"
        )
    return "\n".join(lines)


def _h(value: object) -> str:
    if value is None:
        return "—"
    return escape(str(value), quote=False)


def format_order_blockquote(
    order: BuyoutOrder,
    history: list[OrderStatusHistoryItem],
    *,
    header_line: str | None = None,
    extra_lines: list[str] | None = None,
) -> str:
    order_lines = [header_line or f"<b>Выкуп №{_h(order.order_number)}</b>"]
    if extra_lines:
        order_lines.extend(extra_lines)
    order_lines.extend(
        [
            f"Статус: <b>{_h(order_status_title(order.status))}</b> ({order.updated_at.strftime('%d.%m.%y')})",
            f"Цена: {_h(order.price_rub if order.price_rub is not None else '—')}",
            f"Ссылка: {_h(order.product_url)}",
            f"Детали: {_h(order.quantity_text)}",
            f"Комментарий: {_h(order.manager_comment or '—')}",
            f"Трек: {_h(order.track_number or '—')}",
            format_order_history_short(history),
        ]
    )
    order_block = "\n".join(order_lines)
    return f"<blockquote expandable>{order_block}</blockquote>"
