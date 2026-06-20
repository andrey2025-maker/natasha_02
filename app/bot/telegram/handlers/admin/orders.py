from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.handlers.admin.html import _h
from app.bot.telegram.handlers.admin.media_helpers import _mark_blocked_bot_if_needed
from app.core.container import AppContainer
from app.domain.enums import OrderStatus, Platform
from app.domain.models import OutboundMessage
from app.services.admin_tools_service import PaymentTextStore, send_stored_media_to_telegram

from app.bot.telegram.handlers.admin.keyboards import _orders_root_keyboard

async def _send_orders_panel(
    message: Message,
    container: AppContainer,
    codec: CallbackCodec,
    user_id: int,
    state: dict,
    edit: bool = False,
) -> None:
    page = int(state.get("page", 1))
    selected = set(state.get("selected", []))
    orders, total = await container.order_admin_service.list_recent_orders(page=page, page_size=9)
    if not orders and page > 1:
        page = page - 1
        state["page"] = page
        orders, total = await container.order_admin_service.list_recent_orders(page=page, page_size=9)
    total_pages = max(1, (total + 8) // 9)

    lines = ["<b>Заказы (массовое обновление)</b>"]
    lines.append(f"Страница {page}/{total_pages}")
    lines.append(f"Выбрано: {len(selected)}")
    lines.append("")
    if not orders:
        lines.append("Заказов пока нет.")
    else:
        for order in orders:
            mark = "✅" if order.order_number in selected else "⬜️"
            lines.append(
                f"{mark} {_h(order.order_number)} — {_h(_order_status_name(order.status))} "
                f"({order.updated_at.strftime('%d.%m.%y')})"
            )

    keyboard = _orders_keyboard(user_id, codec, page, total_pages, orders, selected)
    if edit:
        await message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


def _orders_keyboard(
    user_id: int,
    codec: CallbackCodec,
    page: int,
    total_pages: int,
    orders,
    selected: set[str],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for order in orders:
        mark = "✅" if order.order_number in selected else "⬜️"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {order.order_number}",
                    callback_data=codec.encode(f"admin:orders:toggle:{order.order_number}", user_id),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Ожидание",
                callback_data=codec.encode("admin:orders:set_status:pending", user_id),
            ),
            InlineKeyboardButton(
                text="Оплачен",
                callback_data=codec.encode("admin:orders:set_status:paid", user_id),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="В пути",
                callback_data=codec.encode("admin:orders:set_status:in_transit", user_id),
            ),
            InlineKeyboardButton(
                text="ПВЗ",
                callback_data=codec.encode("admin:orders:set_status:pickup_point", user_id),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Выдан",
                callback_data=codec.encode("admin:orders:set_status:issued", user_id),
            ),
            InlineKeyboardButton(
                text="Отменен",
                callback_data=codec.encode("admin:orders:set_status:cancelled", user_id),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Очистить выбор",
                callback_data=codec.encode("admin:orders:clear", user_id),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Редактировать выбранный",
                callback_data=codec.encode("admin:orders:edit", user_id),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Массово: Цена",
                callback_data=codec.encode("admin:orders:bulk_field:price_rub", user_id),
            ),
            InlineKeyboardButton(
                text="Массово: Комментарий",
                callback_data=codec.encode("admin:orders:bulk_field:manager_comment", user_id),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Массово: Трек",
                callback_data=codec.encode("admin:orders:bulk_field:track_number", user_id),
            ),
            InlineKeyboardButton(
                text="Массово: Детали",
                callback_data=codec.encode("admin:orders:bulk_field:quantity_text", user_id),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️",
                callback_data=codec.encode(f"admin:orders:page:{max(1, page - 1)}", user_id),
            ),
            InlineKeyboardButton(
                text="➡️",
                callback_data=codec.encode(f"admin:orders:page:{min(total_pages, page + 1)}", user_id),
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _get_admin_orders_state(session) -> dict:
    block = session.state_data.get("_admin_orders")
    if isinstance(block, dict):
        page = int(block.get("page", 1))
        selected = block.get("selected", [])
        edit_order = block.get("edit_order")
        edit_field = block.get("edit_field")
        if isinstance(selected, list):
            return {
                "page": page,
                "selected": [str(item) for item in selected],
                "edit_order": str(edit_order) if edit_order else None,
                "edit_field": str(edit_field) if edit_field else None,
                "bulk_field": str(block.get("bulk_field")) if block.get("bulk_field") else None,
                "pending_field": str(block.get("pending_field")) if block.get("pending_field") else None,
                "pending_value": str(block.get("pending_value")) if block.get("pending_value") else None,
            }
    return {
        "page": 1,
        "selected": [],
        "edit_order": None,
        "edit_field": None,
        "bulk_field": None,
        "pending_field": None,
        "pending_value": None,
    }


async def _save_admin_orders_state(container: AppContainer, session, state: dict) -> None:
    payload = dict(session.state_data)
    payload["_admin_orders"] = {
        "page": int(state.get("page", 1)),
        "selected": list(state.get("selected", [])),
        "edit_order": state.get("edit_order"),
        "edit_field": state.get("edit_field"),
        "bulk_field": state.get("bulk_field"),
        "pending_field": state.get("pending_field"),
        "pending_value": state.get("pending_value"),
    }
    session.state_data = payload
    await container.session_repo.save(session)

async def _notify_order_status_change(
    trigger_message: Message,
    container: AppContainer,
    payment_store: PaymentTextStore,
    codec: CallbackCodec,
    order,
    new_status: OrderStatus,
    note: str = "",
) -> None:
    profile = await container.profile_repo.get_by_id(order.user_profile_id)
    if not profile:
        return
    text = (
        f"Обновление по заказу <b>№{_h(order.order_number)}</b>.\n"
        f"Новый статус: <b>{_h(_order_status_name(new_status))}</b>."
    )
    if note.strip():
        text += f"\nКомментарий: {_h(note.strip())}"
    payment_media_items: list[dict] = []
    if new_status == OrderStatus.WAITING_PAYMENT:
        payment_text = await payment_store.get_text()
        text += "\n\n" + payment_text
        payment_media_items = await payment_store.get_media_items()
    if profile.telegram_user_id:
        try:
            reply_markup = None
            if new_status == OrderStatus.WAITING_PAYMENT:
                reply_markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Оплачено",
                                callback_data=codec.encode(
                                    f"orderpay:paid:{order.order_number}",
                                    int(profile.telegram_user_id),
                                ),
                            ),
                            InlineKeyboardButton(
                                text="❌ Отмена",
                                callback_data=codec.encode(
                                    f"orderpay:cancel:{order.order_number}",
                                    int(profile.telegram_user_id),
                                ),
                            ),
                        ]
                    ]
                )
            await trigger_message.bot.send_message(
                chat_id=profile.telegram_user_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            for payment_media in payment_media_items:
                await send_stored_media_to_telegram(
                    trigger_message.bot,
                    int(profile.telegram_user_id),
                    payment_media,
                )
        except Exception as exc:
            await _mark_blocked_bot_if_needed(container, profile, exc)
    if profile.vk_user_id:
        await container.outbound_repo.enqueue(
            OutboundMessage(
                id=0,
                platform=Platform.VK,
                platform_user_id=int(profile.vk_user_id),
                message_type="plain_text",
                payload={"text": text},
            )
        )

async def _send_order_edit_panel(
    message: Message,
    container: AppContainer,
    user_id: int,
    codec: CallbackCodec,
    state: dict,
    edit: bool = False,
) -> None:
    order_number = str(state.get("edit_order") or "")
    order = await container.order_admin_service.get_order(order_number) if order_number else None
    if not order:
        text = "Заказ не найден. Вернитесь к списку."
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К списку", callback_data=codec.encode("admin:orders:back_list", user_id))]
            ]
        )
        if edit:
            await message.edit_text(text, reply_markup=keyboard)
        else:
            await message.answer(text, reply_markup=keyboard)
        return

    pending_field = state.get("pending_field")
    pending_value = state.get("pending_value")
    text = (
        f"<b>Редактирование заказа {_h(order_number)}</b>\n"
        f"Статус: <b>{_h(_order_status_name(order.status))}</b>\n"
        f"Ссылка: <code>{_h(order.product_url)}</code>\n"
        f"Детали: <code>{_h(order.quantity_text)}</code>\n"
        f"Цена: <code>{_h(order.price_rub if order.price_rub is not None else '—')}</code>\n"
        f"Комментарий: <code>{_h(order.manager_comment or '—')}</code>\n"
        f"Трек: <code>{_h(order.track_number or '—')}</code>\n\n"
        "Выберите поле и отправьте новое значение следующим сообщением."
    )
    if pending_field and pending_value:
        text += (
            "\n\n"
            f"Ожидает подтверждения: <b>{_h(_field_title(str(pending_field)))}</b> = "
            f"<code>{_h(pending_value)}</code>"
        )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Ссылка", callback_data=codec.encode("admin:orders:edit_field:product_url", user_id))],
            [InlineKeyboardButton(text="Количество/размер/цвет", callback_data=codec.encode("admin:orders:edit_field:quantity_text", user_id))],
            [InlineKeyboardButton(text="Цена", callback_data=codec.encode("admin:orders:edit_field:price_rub", user_id))],
            [InlineKeyboardButton(text="Комментарий", callback_data=codec.encode("admin:orders:edit_field:manager_comment", user_id))],
            [InlineKeyboardButton(text="Трек", callback_data=codec.encode("admin:orders:edit_field:track_number", user_id))],
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить изменение",
                    callback_data=codec.encode("admin:orders:confirm_apply", user_id),
                ),
                InlineKeyboardButton(
                    text="❌ Сбросить изменение",
                    callback_data=codec.encode("admin:orders:confirm_cancel", user_id),
                ),
            ],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data=codec.encode("admin:orders:back_list", user_id))],
        ]
    )
    if edit:
        await message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


def _parse_order_status(raw: str) -> OrderStatus | None:
    key = raw.strip().lower()
    aliases = {
        "pending": OrderStatus.PENDING,
        "price_ready": OrderStatus.PRICE_READY,
        "waiting_payment": OrderStatus.WAITING_PAYMENT,
        "paid_check": OrderStatus.PAID_CHECK,
        "paid": OrderStatus.PAID,
        "in_transit": OrderStatus.IN_TRANSIT,
        "pickup_point": OrderStatus.PICKUP_POINT,
        "issued": OrderStatus.ISSUED,
        "cancelled": OrderStatus.CANCELLED,
        "ожидание": OrderStatus.PENDING,
        "цена": OrderStatus.PRICE_READY,
        "оплата": OrderStatus.WAITING_PAYMENT,
        "проверка": OrderStatus.PAID_CHECK,
        "оплачен": OrderStatus.PAID,
        "впути": OrderStatus.IN_TRANSIT,
        "пвз": OrderStatus.PICKUP_POINT,
        "pickup_point": OrderStatus.PICKUP_POINT,
        "выдан": OrderStatus.ISSUED,
        "issued": OrderStatus.ISSUED,
        "отменен": OrderStatus.CANCELLED,
        "cancelled": OrderStatus.CANCELLED,
        "in_transit": OrderStatus.IN_TRANSIT,
    }
    return aliases.get(key)


def _order_status_name(status: OrderStatus) -> str:
    names = {
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
    return names.get(status, status.value)


def _field_title(field_name: str) -> str:
    titles = {
        "product_url": "Ссылка",
        "quantity_text": "Количество/размер/цвет",
        "price_rub": "Цена",
        "manager_comment": "Комментарий",
        "track_number": "Трек",
    }
    return titles.get(field_name, field_name)


def _order_field_value(order, field_name: str) -> str:
    if order is None:
        return "—"
    mapping = {
        "product_url": order.product_url,
        "quantity_text": order.quantity_text,
        "price_rub": str(order.price_rub) if order.price_rub is not None else "",
        "manager_comment": order.manager_comment or "",
        "track_number": order.track_number or "",
    }
    return str(mapping.get(field_name, ""))


def _validate_field_input(field_name: str, raw_value: str) -> tuple[bool, str]:
    value = raw_value.strip()
    if field_name == "product_url":
        if not value.startswith(("http://", "https://")):
            return False, "Ссылка должна начинаться с http:// или https://"
        return True, value
    if field_name == "quantity_text":
        if not value:
            return False, "Поле деталей не может быть пустым."
        if len(value) > 250:
            return False, "Слишком длинное значение для деталей (максимум 250 символов)."
        return True, value
    if field_name == "price_rub":
        normalized = value.replace(" ", "")
        if not normalized:
            return True, ""
        if not normalized.isdigit():
            return False, "Цена должна содержать только цифры."
        amount = int(normalized)
        if amount < 0 or amount > 1_000_000_000:
            return False, "Цена вне допустимого диапазона."
        return True, str(amount)
    if field_name == "manager_comment":
        if len(value) > 2000:
            return False, "Комментарий слишком длинный (максимум 2000 символов)."
        return True, value
    if field_name == "track_number":
        if len(value) > 128:
            return False, "Трек слишком длинный (максимум 128 символов)."
        return True, value
    return False, "Неизвестное поле для редактирования."
