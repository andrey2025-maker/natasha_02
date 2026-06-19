from __future__ import annotations

import json

from app.domain.enums import OrderStatus


from app.domain.models import UserProfile


def start_keyboard() -> str:
    return _keyboard(
        [
            ["Профиль", "Как работает доставка"],
            ["Запрещенные товары", "Наши контакты"],
            ["Заказ выкупа", "Мои заказы"],
            ["Вопросы"],
            ["Фильтры заказов"],
        ]
    )


def profile_menu_keyboard(profile: UserProfile | None = None) -> str:
    rows: list[list[str]] = []
    if not (profile and profile.is_filled):
        rows.append(["Заполнить профиль", "Есть профиль ТГ"])
    rows.extend(
        [
            ["Заказ выкупа", "Мои заказы"],
            ["Вопросы"],
            ["Фильтры заказов"],
        ]
    )
    return _keyboard(rows)


def profile_confirm_keyboard() -> str:
    return _keyboard(
        [
            ["Да"],
            ["Имя", "Тел.", "Город"],
        ]
    )


def yes_no_keyboard() -> str:
    return _keyboard([["Да", "Нет"]])


def code_confirm_keyboard() -> str:
    return _keyboard([["Да", "Исправить"]])


def buyout_add_more_keyboard() -> str:
    return _keyboard([["Ещё товар", "Нет"]])


def status_filters_keyboard(enabled: dict[OrderStatus, bool]) -> str:
    rows: list[list[str]] = []
    for status in OrderStatus:
        emoji = "🟢" if enabled.get(status, True) else "🔴"
        rows.append([f"Фильтр {emoji} {_short_status_name(status)}"])
    rows.append(["Фильтр все"])
    return _keyboard(rows)


def _keyboard(rows: list[list[str]]) -> str:
    payload = {
        "one_time": False,
        "inline": False,
        "buttons": [
            [
                {
                    "action": {"type": "text", "label": label},
                    "color": "secondary",
                }
                for label in row
            ]
            for row in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _short_status_name(status: OrderStatus) -> str:
    names = {
        OrderStatus.PENDING: "Ожидание",
        OrderStatus.PRICE_READY: "Цена",
        OrderStatus.WAITING_PAYMENT: "Оплата",
        OrderStatus.PAID_CHECK: "Проверка",
        OrderStatus.PAID: "Оплачен",
        OrderStatus.IN_TRANSIT: "В пути",
        OrderStatus.PICKUP_POINT: "ПВЗ",
        OrderStatus.ISSUED: "Выдан",
        OrderStatus.CANCELLED: "Отменен",
    }
    return names.get(status, status.value)
