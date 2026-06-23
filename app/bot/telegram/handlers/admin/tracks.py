from __future__ import annotations

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.telegram.callback_panel import edit_panel_message
from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.handlers.admin.orders import _notify_order_status_change, _order_status_name, _parse_order_status
from app.core.container import AppContainer
from app.domain.enums import OrderStatus, Platform
from app.services.admin_tools_service import PaymentTextStore
from app.services.order_filter_config import ORDER_FILTER_BUTTONS_PER_ROW, ORDER_FILTER_EMOJI, ORDER_FILTER_STATUSES
from app.services.track_match_utils import match_uploaded_tracks, parse_txt_track_lines

_TRACK_STATUS_LABELS: dict[OrderStatus, str] = {
    OrderStatus.PENDING: "Ожидание",
    OrderStatus.ISSUED: "Выдан",
    OrderStatus.PICKUP_POINT: "ПВЗ",
    OrderStatus.IN_TRANSIT: "В пути",
    OrderStatus.PAID: "Оплачен",
    OrderStatus.PAID_CHECK: "Проверка",
    OrderStatus.CANCELLED: "Отменён",
    OrderStatus.PRICE_READY: "Цена готова",
    OrderStatus.WAITING_PAYMENT: "Ожидает оплату",
}


def _default_admin_tracks_state() -> dict:
    return {
        "awaiting_txt_file": False,
        "awaiting_status_comment": False,
        "uploaded_total": 0,
        "matched_count": 0,
        "matched_order_numbers": [],
        "pending_status": None,
    }


def _get_admin_tracks_state(session) -> dict:
    block = session.state_data.get("_admin_tracks")
    if isinstance(block, dict):
        return {
            "awaiting_txt_file": bool(block.get("awaiting_txt_file")),
            "awaiting_status_comment": bool(block.get("awaiting_status_comment")),
            "uploaded_total": int(block.get("uploaded_total") or 0),
            "matched_count": int(block.get("matched_count") or 0),
            "matched_order_numbers": [
                str(item) for item in block.get("matched_order_numbers", []) if str(item).strip()
            ],
            "pending_status": str(block.get("pending_status")) if block.get("pending_status") else None,
        }
    return _default_admin_tracks_state()


async def _save_admin_tracks_state(container: AppContainer, session, state: dict) -> None:
    payload = dict(session.state_data)
    payload["_admin_tracks"] = {
        "awaiting_txt_file": bool(state.get("awaiting_txt_file")),
        "awaiting_status_comment": bool(state.get("awaiting_status_comment")),
        "uploaded_total": int(state.get("uploaded_total") or 0),
        "matched_count": int(state.get("matched_count") or 0),
        "matched_order_numbers": list(state.get("matched_order_numbers") or []),
        "pending_status": state.get("pending_status"),
    }
    session.state_data = payload
    await container.session_repo.save(session)


def reset_admin_tracks_state(state: dict) -> None:
    state.clear()
    state.update(_default_admin_tracks_state())


def admin_tracks_has_pending(session) -> bool:
    tracks_state = _get_admin_tracks_state(session)
    return bool(
        tracks_state.get("awaiting_txt_file")
        or tracks_state.get("awaiting_status_comment")
        or tracks_state.get("pending_status")
    )


def tracks_root_text() -> str:
    return (
        "<b>🔖 Управление треками</b>\n"
        "Выберите способ работы:"
    )


def tracks_txt_prompt_text() -> str:
    return (
        "<b>📄 Загрузка из TXT</b>\n"
        "Отправьте файл в формате .txt с трек-номерами."
    )


def tracks_scanner_text() -> str:
    return (
        "Нихуя не понял по платформам PRO-Cargo и 1999CARGO объяснишь — сделаем тут сканер."
    )


def tracks_match_result_text(matched_count: int, uploaded_total: int) -> str:
    return (
        "<b>🔍 Результат проверки</b>\n"
        f"Найдено совпадений: <b>{matched_count}</b> из <b>{uploaded_total}</b> загруженных.\n\n"
        "Что сделать с найденными заказами?\n"
        "Выберите новый статус:"
    )


def tracks_comment_prompt_text() -> str:
    return (
        "<b>✏️ Хотите добавить комментарий к статусу?</b>\n"
        "Например: «В пути — Склад Астаны»"
    )


def tracks_done_text(*, changed: int, status: OrderStatus, note: str = "") -> str:
    emoji = ORDER_FILTER_EMOJI.get(status, "•")
    label = _TRACK_STATUS_LABELS.get(status, _order_status_name(status))
    status_line = f"{emoji} {label}"
    if note.strip():
        status_line = f"{status_line} — {note.strip()}"
    return (
        "<b>🎉 Готово! Обновление завершено</b>\n\n"
        f"✨ <b>{changed}</b> заказов теперь в статусе:\n"
        f"{status_line}"
    )


def _tracks_root_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📄 TXT файл",
                    callback_data=codec.encode("admin:tracks:txt", user_id),
                ),
                InlineKeyboardButton(
                    text="📷 Сканер",
                    callback_data=codec.encode("admin:tracks:scanner", user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В админку",
                    callback_data=codec.encode("admin:tracks:back", user_id),
                ),
            ],
        ]
    )


def _tracks_back_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=codec.encode("admin:tracks:root", user_id),
                ),
            ],
        ]
    )


def _tracks_status_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    statuses = list(ORDER_FILTER_STATUSES)
    for index in range(0, len(statuses), ORDER_FILTER_BUTTONS_PER_ROW):
        chunk = statuses[index : index + ORDER_FILTER_BUTTONS_PER_ROW]
        rows.append(
            [
                InlineKeyboardButton(
                    text=_track_status_button_text(status),
                    callback_data=codec.encode(f"admin:tracks:set_status:{status.value}", user_id),
                )
                for status in chunk
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=codec.encode("admin:tracks:root", user_id),
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _tracks_comment_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Добавить комментарий",
                    callback_data=codec.encode("admin:tracks:comment:yes", user_id),
                ),
                InlineKeyboardButton(
                    text="🚫 Нет, просто статус",
                    callback_data=codec.encode("admin:tracks:comment:no", user_id),
                ),
            ],
        ]
    )


def _track_status_button_text(status: OrderStatus) -> str:
    emoji = ORDER_FILTER_EMOJI.get(status, "•")
    label = _TRACK_STATUS_LABELS.get(status, status.value)
    return f"{emoji} {label}"


async def open_admin_tracks_panel(message: Message, *, user_id: int, codec: CallbackCodec) -> None:
    await edit_panel_message(
        message,
        text=tracks_root_text(),
        reply_markup=_tracks_root_keyboard(user_id, codec),
    )


async def process_tracks_txt_upload(
    message: Message,
    *,
    container: AppContainer,
    codec: CallbackCodec,
    session,
    content: str,
) -> None:
    if not message.from_user:
        return
    uploaded = parse_txt_track_lines(content)
    if not uploaded:
        await message.answer("Файл пустой или не содержит трек-номеров.")
        return

    orders = await container.buyout_repo.list_orders_with_exact_tracks(uploaded)
    matched_count, uploaded_total, order_numbers = match_uploaded_tracks(uploaded, orders)

    state = _get_admin_tracks_state(session)
    state["awaiting_txt_file"] = False
    state["awaiting_status_comment"] = False
    state["pending_status"] = None
    state["uploaded_total"] = uploaded_total
    state["matched_count"] = matched_count
    state["matched_order_numbers"] = order_numbers
    await _save_admin_tracks_state(container, session, state)

    if matched_count <= 0:
        await message.answer(
            (
                "<b>🔍 Результат проверки</b>\n"
                f"Найдено совпадений: <b>0</b> из <b>{uploaded_total}</b> загруженных.\n\n"
                "Совпадающих заказов в боте нет."
            ),
            parse_mode="HTML",
            reply_markup=_tracks_back_keyboard(message.from_user.id, codec),
        )
        return

    await message.answer(
        tracks_match_result_text(matched_count, uploaded_total),
        parse_mode="HTML",
        reply_markup=_tracks_status_keyboard(message.from_user.id, codec),
    )


async def apply_tracks_status_update(
    *,
    trigger: Message | CallbackQuery,
    container: AppContainer,
    payment_store: PaymentTextStore,
    codec: CallbackCodec,
    session,
    status: OrderStatus,
    note: str = "",
) -> int:
    if isinstance(trigger, CallbackQuery):
        message = trigger.message
        user_id = trigger.from_user.id if trigger.from_user else 0
    else:
        message = trigger
        user_id = trigger.from_user.id if trigger.from_user else 0
    if not message or not user_id:
        return 0

    tracks_state = _get_admin_tracks_state(session)
    order_numbers = list(tracks_state.get("matched_order_numbers") or [])
    if not order_numbers:
        return 0

    changed = 0
    for order_number in order_numbers:
        updated = await container.order_admin_service.set_status(
            order_number=order_number,
            new_status=status,
            changed_by_user_id=user_id,
            note=note.strip(),
            platform=Platform.TELEGRAM,
        )
        if updated:
            changed += 1
            await _notify_order_status_change(
                trigger_message=message,
                container=container,
                payment_store=payment_store,
                codec=codec,
                order=updated,
                new_status=status,
                note=note.strip(),
            )

    reset_admin_tracks_state(tracks_state)
    await _save_admin_tracks_state(container, session, tracks_state)
    return changed


async def try_handle_admin_tracks_document(
    message: Message,
    *,
    container: AppContainer,
    codec: CallbackCodec,
    session,
) -> bool:
    if not message.document or not message.from_user:
        return False
    tracks_state = _get_admin_tracks_state(session)
    if not tracks_state.get("awaiting_txt_file"):
        return False

    file_name = (message.document.file_name or "").strip().lower()
    if not file_name.endswith(".txt"):
        await message.answer("Нужен файл с расширением .txt")
        return True

    try:
        tg_file = await message.bot.get_file(message.document.file_id)
        if not tg_file or not tg_file.file_path:
            await message.answer("Не удалось получить файл.")
            return True
        downloaded = await message.bot.download_file(tg_file.file_path)
        raw = downloaded.read()
        content = raw.decode("utf-8-sig", errors="replace")
    except Exception:
        await message.answer("Не удалось прочитать файл. Проверьте кодировку UTF-8.")
        return True

    await process_tracks_txt_upload(
        message,
        container=container,
        codec=codec,
        session=session,
        content=content,
    )
    return True


async def try_handle_admin_tracks_text(
    message: Message,
    *,
    container: AppContainer,
    payment_store: PaymentTextStore,
    codec: CallbackCodec,
    session,
) -> bool:
    if not message.text or not message.from_user:
        return False
    tracks_state = _get_admin_tracks_state(session)
    if not tracks_state.get("awaiting_status_comment"):
        return False

    status_raw = str(tracks_state.get("pending_status") or "")
    status = _parse_order_status(status_raw)
    if status is None:
        reset_admin_tracks_state(tracks_state)
        await _save_admin_tracks_state(container, session, tracks_state)
        await message.answer("Сессия устарела. Откройте «Трэки» заново.")
        return True

    note = message.text.strip()
    if not note:
        await message.answer("Комментарий не может быть пустым. Или нажмите «Нет, просто статус».")
        return True

    changed = await apply_tracks_status_update(
        trigger=message,
        container=container,
        payment_store=payment_store,
        codec=codec,
        session=session,
        status=status,
        note=note,
    )
    await message.answer(
        tracks_done_text(changed=changed, status=status, note=note),
        parse_mode="HTML",
        reply_markup=_tracks_back_keyboard(message.from_user.id, codec),
    )
    return True
