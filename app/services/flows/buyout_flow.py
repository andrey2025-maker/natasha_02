from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from urllib.parse import urlparse

from app.domain.enums import DeliveryFlowType, DialogState, OrderStatus, Platform
from app.domain.models import BuyoutOrder, OrderMediaItem, OrderStatusHistoryItem, UserProfile, UserSession
from app.services.user_preferences_store import UserPreferencesStore
from app.storage.interfaces import BuyoutOrderRepository, SessionRepository, UserProfileRepository


@dataclass(slots=True)
class BuyoutFlowResponse:
    text: str
    state: DialogState
    state_data: dict
    reply_markup: object | None = None


class BuyoutFlowService:
    def __init__(
        self,
        profile_repo: UserProfileRepository,
        session_repo: SessionRepository,
        order_repo: BuyoutOrderRepository,
        preferences_store: UserPreferencesStore,
    ) -> None:
        self._profiles = profile_repo
        self._sessions = session_repo
        self._orders = order_repo
        self._preferences = preferences_store

    async def start(self, session: UserSession) -> BuyoutFlowResponse:
        await self._hydrate_preferences(session)
        profile = await self._profiles.get_by_platform_user(session.platform, session.platform_user_id)
        if not profile:
            return BuyoutFlowResponse(
                text="Сначала заполните профиль, затем можно оформить выкуп.",
                state=DialogState.IDLE,
                state_data={},
            )
        session.state = DialogState.BUYOUT_WAIT_MEDIA
        session.state_data = self._merge_preferences(session.state_data, self._get_preferences(session))
        await self._sessions.save(session)
        return BuyoutFlowResponse(
            text=(
                "💰 <b>Заказать выкуп товара</b>\n"
                "Для заказа выкупа отправьте <b>скриншот</b> товара с выбранными "
                "<b>цветом</b>, <b>размером</b> и <b>количеством</b>.\n"
                "Можно отправить фото, видео или гиф."
            ),
            state=session.state,
            state_data={},
        )

    async def handle_media(
        self,
        session: UserSession,
        media_group_id: str | None,
        storage_chat_id: int | None = None,
        storage_topic_id: int | None = None,
        storage_message_id: int | None = None,
        vk_attachment: str | None = None,
        media_type: str | None = None,
        tg_file_id: str | None = None,
        media_items: list[dict] | None = None,
    ) -> BuyoutFlowResponse:
        if session.state != DialogState.BUYOUT_WAIT_MEDIA:
            return BuyoutFlowResponse("Сначала начните новый заказ выкупа.", session.state, dict(session.state_data))
        state_data = dict(session.state_data)
        state_data["media_group_id"] = media_group_id
        state_data["media_storage_chat_id"] = int(storage_chat_id) if storage_chat_id else None
        state_data["media_storage_topic_id"] = int(storage_topic_id) if storage_topic_id else None
        state_data["media_storage_message_id"] = int(storage_message_id) if storage_message_id else None
        state_data["media_vk_attachment"] = (vk_attachment or "").strip() or None
        existing_items_raw = state_data.get("order_media_items")
        existing_items = list(existing_items_raw) if isinstance(existing_items_raw, list) else []
        if media_items:
            existing_items.extend(media_items)
        else:
            existing_items.append(
                {
                    "platform": session.platform.value,
                    "media_type": (media_type or "unknown").strip() or "unknown",
                    "tg_chat_id": int(storage_chat_id) if storage_chat_id else None,
                    "tg_topic_id": int(storage_topic_id) if storage_topic_id else None,
                    "tg_message_id": int(storage_message_id) if storage_message_id else None,
                    "tg_file_id": (tg_file_id or "").strip() or None,
                    "vk_attachment": (vk_attachment or "").strip() or None,
                }
            )
        state_data["order_media_items"] = _dedupe_media_items(existing_items)
        session.state = DialogState.BUYOUT_WAIT_LINK
        session.state_data = state_data
        await self._sessions.save(session)
        return BuyoutFlowResponse(
            text="Отправьте <b>ссылку на товар</b>:",
            state=session.state,
            state_data=state_data,
        )

    async def handle_text(self, session: UserSession, text: str) -> BuyoutFlowResponse:
        await self._hydrate_preferences(session)
        data = dict(session.state_data)
        if session.state == DialogState.BUYOUT_WAIT_LINK:
            if not _is_likely_url(text):
                return BuyoutFlowResponse("Нужна корректная ссылка на товар.", session.state, data)
            data["product_url"] = text.strip()
            session.state = DialogState.BUYOUT_WAIT_DETAILS
            session.state_data = data
            await self._sessions.save(session)
            return BuyoutFlowResponse(
                text=(
                    "Напишите <b>количество</b> и, если есть, <b>размер</b> и <b>цвет</b>.\n"
                    "<b>(пример: 2 шт, S, синий)</b>"
                ),
                state=session.state,
                state_data=data,
            )

        if session.state == DialogState.BUYOUT_WAIT_DETAILS:
            data["quantity_text"] = text.strip()
            profile = await self._profiles.get_by_platform_user(session.platform, session.platform_user_id)
            if not profile:
                return BuyoutFlowResponse("Профиль не найден, начните заново.", DialogState.IDLE, {})

            order_number = await self._next_order_number(profile, data["product_url"])
            now = datetime.utcnow()
            order = BuyoutOrder(
                id=0,
                user_profile_id=profile.id,
                order_number=order_number,
                flow_type=DeliveryFlowType.BUYOUT,
                status=OrderStatus.PENDING,
                product_url=data["product_url"],
                quantity_text=data["quantity_text"],
                media_group_id=data.get("media_group_id"),
                media_storage_chat_id=data.get("media_storage_chat_id"),
                media_storage_topic_id=data.get("media_storage_topic_id"),
                media_storage_message_id=data.get("media_storage_message_id"),
                media_vk_attachment=data.get("media_vk_attachment"),
                track_number=None,
                created_at=now,
                updated_at=now,
            )
            saved_order = await self._orders.create(order)
            await self._save_order_media_items(saved_order.id, data.get("order_media_items"))
            session.state = DialogState.BUYOUT_ADD_MORE
            session.state_data = self._merge_preferences(session.state_data, self._get_preferences(session))
            await self._sessions.save(session)
            return BuyoutFlowResponse(
                text=(
                    "Данные отправлены на обработку цены.\n"
                    "<b>Добавить еще товар на выкуп?</b>\n"
                    "Ответьте: «Ещё товар» или «Нет»."
                ),
                state=session.state,
                state_data={},
            )

        if session.state == DialogState.BUYOUT_ADD_MORE:
            normalized = text.strip().lower()
            if normalized in {"ещё товар", "еще товар"}:
                return await self.start(session)
            if normalized in {"нет", "no"}:
                session.state = DialogState.IDLE
                session.state_data = self._merge_preferences(session.state_data, self._get_preferences(session))
                await self._sessions.save(session)
                return BuyoutFlowResponse(
                    text="Хорошо, заявку сохранил. Когда будете готовы — отправьте новый заказ.",
                    state=session.state,
                    state_data={},
                )
        return BuyoutFlowResponse("Не понял ответ. Используйте кнопки или текст из подсказки.", session.state, data)

    async def _next_order_number(self, profile: UserProfile, product_url: str) -> str:
        count = await self._orders.count_for_user(profile.id)
        marketplace_letter = _marketplace_letter(product_url)
        return f"{profile.code}/{count + 1}{marketplace_letter}"

    async def render_orders(self, session: UserSession, page: int = 1, page_size: int = 9) -> BuyoutFlowResponse:
        await self._hydrate_preferences(session)
        profile = await self._profiles.get_by_platform_user(session.platform, session.platform_user_id)
        if not profile:
            return BuyoutFlowResponse("Сначала заполните профиль.", DialogState.IDLE, {})

        statuses = self._get_active_filter_values(session)
        all_orders = await self._orders.list_for_user(profile.id, limit=1000, offset=0, statuses=statuses)
        total = len(all_orders)
        if total == 0:
            return BuyoutFlowResponse("По текущим фильтрам заказов нет.", DialogState.IDLE, {})

        safe_page = max(1, page)
        offset = (safe_page - 1) * page_size
        orders = await self._orders.list_for_user(profile.id, limit=page_size, offset=offset, statuses=statuses)
        if not orders and safe_page > 1:
            safe_page -= 1
            offset = (safe_page - 1) * page_size
            orders = await self._orders.list_for_user(profile.id, limit=page_size, offset=offset, statuses=statuses)

        lines: list[str] = []
        for idx, order in enumerate(orders, start=1 + offset):
            history = await self._orders.list_status_history(order.id, limit=3)
            history_text = _format_history_short(history)
            media_items = await self._orders.list_order_media(order.id)
            media_indexes = ",".join(str(i) for i in range(1, len(media_items) + 1)) if media_items else ""
            order_lines = [f"<b>Выкуп №{_h(order.order_number)}</b>"]
            if media_indexes:
                order_lines.append(f"Медиа: {_h(media_indexes)}")
            order_lines.extend(
                [
                    f"Статус: <b>{_h(_status_title(order.status))}</b> ({order.updated_at.strftime('%d.%m.%y')})",
                    f"Цена: {_h(order.price_rub if order.price_rub is not None else '—')}",
                    f"Ссылка: {_h(order.product_url)}",
                    f"Детали: {_h(order.quantity_text)}",
                    f"Комментарий: {_h(order.manager_comment or '—')}",
                    f"Трек: {_h(order.track_number or '—')}",
                    history_text,
                ]
            )
            lines.append(
                f"{idx}.\n"
                f"<tg-spoiler>{'\n'.join(order_lines)}</tg-spoiler>"
            )

        total_pages = (total + page_size - 1) // page_size
        filters_text = self._filters_summary_text(session)
        text = (
            "Ваши заказы:\n"
            f"{filters_text}\n\n"
            + "\n\n".join(lines)
            + f"\n\nСтраница {safe_page}/{total_pages}"
        )
        return BuyoutFlowResponse(
            text=text,
            state=DialogState.IDLE,
            state_data={"page": safe_page, "total_pages": total_pages},
        )

    async def toggle_status_filter(self, session: UserSession, status: OrderStatus) -> None:
        await self._hydrate_preferences(session)
        prefs = self._get_preferences(session)
        active = set(prefs.get("order_filters", [item.value for item in OrderStatus]))
        if status.value in active:
            active.discard(status.value)
        else:
            active.add(status.value)
        if not active:
            active = {item.value for item in OrderStatus}
        prefs["order_filters"] = sorted(active)
        session.state_data = self._merge_preferences(session.state_data, prefs)
        await self._sessions.save(session)
        await self._preferences.save_order_filters(
            platform=session.platform,
            platform_user_id=session.platform_user_id,
            filters=list(prefs["order_filters"]),
        )

    async def reset_status_filters(self, session: UserSession) -> None:
        await self._hydrate_preferences(session)
        prefs = self._get_preferences(session)
        prefs["order_filters"] = [item.value for item in OrderStatus]
        session.state_data = self._merge_preferences(session.state_data, prefs)
        await self._sessions.save(session)
        await self._preferences.save_order_filters(
            platform=session.platform,
            platform_user_id=session.platform_user_id,
            filters=list(prefs["order_filters"]),
        )

    def filter_states(self, session: UserSession) -> dict[OrderStatus, bool]:
        active = set(self._get_active_filter_values(session))
        return {status: status.value in active for status in OrderStatus}

    async def prepare_preferences(self, session: UserSession) -> None:
        await self._hydrate_preferences(session)

    def parse_filter_alias(self, raw: str) -> OrderStatus | None:
        alias = raw.strip().lower()
        for status, labels in _FILTER_ALIASES.items():
            if alias in labels:
                return status
        return None

    def filters_hint_text(self, session: UserSession) -> str:
        summary = self._filters_summary_text(session)
        aliases = ", ".join(sorted({item for labels in _FILTER_ALIASES.values() for item in labels if len(item) <= 6}))
        return (
            f"{summary}\n"
            "Для VK: используйте команду `фильтр &lt;статус&gt;`.\n"
            f"Примеры статусов: {aliases}\n"
            "Сброс: `фильтр все`."
        )

    def _get_active_filter_values(self, session: UserSession) -> list[str]:
        prefs = self._get_preferences(session)
        raw = prefs.get("order_filters")
        if not raw:
            return [item.value for item in OrderStatus]
        values = [str(item) for item in raw if str(item) in {status.value for status in OrderStatus}]
        return values or [item.value for item in OrderStatus]

    def _filters_summary_text(self, session: UserSession) -> str:
        active = set(self._get_active_filter_values(session))
        parts: list[str] = []
        for status in OrderStatus:
            marker = "🟢" if status.value in active else "🔴"
            parts.append(f"{marker} {_status_title(status)}")
        return "Фильтры: " + ", ".join(parts)

    @staticmethod
    def _get_preferences(session: UserSession) -> dict:
        prefs = session.state_data.get("_prefs")
        if isinstance(prefs, dict):
            return dict(prefs)
        return {}

    @staticmethod
    def _merge_preferences(existing: dict, prefs: dict) -> dict:
        merged = dict(existing)
        merged["_prefs"] = prefs
        return merged

    async def _hydrate_preferences(self, session: UserSession) -> None:
        if session.state_data.get("_prefs_loaded"):
            return
        prefs = self._get_preferences(session)
        if not prefs.get("order_filters"):
            stored = await self._preferences.get_order_filters(
                platform=session.platform,
                platform_user_id=session.platform_user_id,
            )
            if stored:
                prefs["order_filters"] = stored
            else:
                prefs["order_filters"] = [item.value for item in OrderStatus]
        merged = self._merge_preferences(session.state_data, prefs)
        merged["_prefs_loaded"] = True
        session.state_data = merged
        await self._sessions.save(session)

    async def _save_order_media_items(self, order_id: int, raw_items: object) -> None:
        if not isinstance(raw_items, list):
            return
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            platform_value = str(raw.get("platform", "")).strip().lower()
            if platform_value not in {Platform.TELEGRAM.value, Platform.VK.value}:
                continue
            media_type = str(raw.get("media_type", "")).strip() or "unknown"
            item = OrderMediaItem(
                id=0,
                order_id=order_id,
                platform=Platform(platform_value),
                media_type=media_type,
                tg_chat_id=_safe_int(raw.get("tg_chat_id")),
                tg_topic_id=_safe_int(raw.get("tg_topic_id")),
                tg_message_id=_safe_int(raw.get("tg_message_id")),
                tg_file_id=_safe_str(raw.get("tg_file_id")),
                vk_attachment=_safe_str(raw.get("vk_attachment")),
            )
            await self._orders.add_order_media(item)


def _marketplace_letter(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "poizon" in host:
        return "P"
    if "taobao" in host:
        return "T"
    if "1688" in host:
        return "1"
    if "xianyu" in host or "goofish" in host:
        return "X"
    if "pinduoduo" in host:
        return "D"
    return "U"


def _is_likely_url(text: str) -> bool:
    value = text.strip()
    if not value.startswith(("http://", "https://")):
        return False
    parsed = urlparse(value)
    return bool(parsed.netloc)


def _status_title(status: OrderStatus) -> str:
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


def _format_history_short(items: list[OrderStatusHistoryItem]) -> str:
    if not items:
        return "История: —"
    lines = ["История:"]
    for item in items:
        prev = _status_title(item.previous_status) if item.previous_status else "—"
        lines.append(
            f"- {item.changed_at.strftime('%d.%m.%y')} {prev} → {_status_title(item.new_status)}"
        )
    return "\n".join(lines)


def _safe_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _dedupe_media_items(items: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = "|".join(
            [
                str(item.get("platform", "")),
                str(item.get("media_type", "")),
                str(item.get("tg_chat_id", "")),
                str(item.get("tg_topic_id", "")),
                str(item.get("tg_message_id", "")),
                str(item.get("tg_file_id", "")),
                str(item.get("vk_attachment", "")),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _h(value: object) -> str:
    if value is None:
        return "—"
    return escape(str(value), quote=False)


_FILTER_ALIASES: dict[OrderStatus, set[str]] = {
    OrderStatus.PENDING: {"ожидание", "pending", "ож"},
    OrderStatus.PRICE_READY: {"цена", "price", "ready"},
    OrderStatus.WAITING_PAYMENT: {"оплата", "waitpay", "ожидает"},
    OrderStatus.PAID_CHECK: {"проверка", "check"},
    OrderStatus.PAID: {"оплачен", "paid"},
    OrderStatus.IN_TRANSIT: {"впути", "transit", "путь"},
    OrderStatus.PICKUP_POINT: {"пвз", "pickup", "пункт"},
    OrderStatus.ISSUED: {"выдан", "issued"},
    OrderStatus.CANCELLED: {"отменен", "cancel"},
}
