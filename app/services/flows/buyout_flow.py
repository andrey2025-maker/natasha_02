from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from urllib.parse import urlparse

from app.services.order_filter_config import (
    DEFAULT_ORDER_FILTER_VALUES,
    ORDER_FILTER_STATUSES,
    order_filter_title,
)
from app.services.order_list_format import assemble_orders_panel_text, format_order_blockquote, order_status_title
from app.services.order_media_utils import collect_order_media_dicts
from app.bot.telegram.fsm_utils import fsm_prompt
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
    order_media_groups: list[tuple[str, list[dict]]] = field(default_factory=list)


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
            text=fsm_prompt(
                "💰 <b>Заказать выкуп товара</b>\n"
                "Для заказа выкупа вам нужно заполнить данные о товаре.\n"
                "Сначала отправьте <b>скриншот</b> товара с выбранными "
                "<b>цветом</b>, <b>размером</b> и <b>количеством</b>.\n"
                "Можно отправить фото, видео, гиф или несколько файлов."
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
            text=fsm_prompt("Отправьте <b>ссылку на товар</b>:"),
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
                text=fsm_prompt(
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
                    "<b>Добавить еще товар на выкуп?</b>"
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

    async def prepare_preferences(self, session: UserSession, *, persist: bool = True) -> None:
        await self._hydrate_preferences(session, persist=persist)

    async def persist_preferences_if_loaded(self, session: UserSession) -> None:
        if session.state_data.get("_prefs_loaded"):
            await self._sessions.save(session)

    async def _load_order_page_details(
        self,
        orders: list[BuyoutOrder],
    ) -> tuple[dict[int, list[OrderStatusHistoryItem]], list[tuple[str, list[dict]]]]:
        if not orders:
            return {}, []
        history_lists, media_lists = await asyncio.gather(
            asyncio.gather(
                *[self._orders.list_status_history(order.id, limit=3) for order in orders]
            ),
            asyncio.gather(
                *[self._orders.list_order_media(order.id) for order in orders]
            ),
        )
        histories = {order.id: history for order, history in zip(orders, history_lists)}
        order_media_groups: list[tuple[str, list[dict]]] = []
        for order, media_items in zip(orders, media_lists):
            media_dicts = collect_order_media_dicts(order, media_items)
            if media_dicts:
                order_media_groups.append((order.order_number, media_dicts))
        return histories, order_media_groups

    async def render_orders(
        self,
        session: UserSession,
        page: int = 1,
        page_size: int = 9,
        *,
        include_details: bool = True,
        profile: UserProfile | None = None,
    ) -> BuyoutFlowResponse:
        if profile is None:
            profile = await self._profiles.get_by_platform_user(session.platform, session.platform_user_id)
        if not profile:
            return BuyoutFlowResponse(
                "Сначала заполните профиль.",
                DialogState.IDLE,
                {"page": 1, "total_pages": 1},
            )

        statuses = self._get_query_statuses(session)
        safe_page = max(1, page)
        offset = (safe_page - 1) * page_size
        total, orders = await asyncio.gather(
            self._orders.count_for_user(profile.id, statuses=statuses),
            self._orders.list_for_user(
                profile.id,
                limit=page_size,
                offset=offset,
                statuses=statuses,
            ),
        )
        if total == 0:
            return BuyoutFlowResponse(
                "По текущим фильтрам заказов нет.",
                DialogState.IDLE,
                {"page": 1, "total_pages": 1},
            )

        if not orders and safe_page > 1:
            safe_page -= 1
            offset = (safe_page - 1) * page_size
            total, orders = await asyncio.gather(
                self._orders.count_for_user(profile.id, statuses=statuses),
                self._orders.list_for_user(
                    profile.id,
                    limit=page_size,
                    offset=offset,
                    statuses=statuses,
                ),
            )

        header_parts = ["<b>Мои заказы</b>", ""]
        total_pages = max(1, (total + page_size - 1) // page_size)
        header_parts.append(f"Страница {safe_page}/{total_pages}")
        if orders and not include_details:
            header_parts.append("<i>Загрузка истории и медиа…</i>")

        order_media_groups: list[tuple[str, list[dict]]] = []
        if include_details:
            histories, order_media_groups = await self._load_order_page_details(orders)
            lines = [format_order_blockquote(order, histories.get(order.id, [])) for order in orders]
        else:
            lines = [format_order_blockquote(order, []) for order in orders]

        text = assemble_orders_panel_text(
            header_parts,
            lines,
            for_media_caption=False,
        )
        return BuyoutFlowResponse(
            text=text,
            state=DialogState.IDLE,
            state_data={"page": safe_page, "total_pages": total_pages},
            order_media_groups=order_media_groups,
        )

    async def toggle_status_filter(self, session: UserSession, status: OrderStatus) -> None:
        if status not in _MY_ORDERS_FILTER_STATUSES:
            return
        await self._hydrate_preferences(session)
        prefs = self._get_preferences(session)
        active = set(prefs.get("order_filters", [item.value for item in _MY_ORDERS_FILTER_STATUSES]))
        if status.value in active:
            active.discard(status.value)
        else:
            active.add(status.value)
        if not active:
            active = {item.value for item in _MY_ORDERS_FILTER_STATUSES}
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
        prefs["order_filters"] = [item.value for item in _MY_ORDERS_FILTER_STATUSES]
        session.state_data = self._merge_preferences(session.state_data, prefs)
        await self._sessions.save(session)
        await self._preferences.save_order_filters(
            platform=session.platform,
            platform_user_id=session.platform_user_id,
            filters=list(prefs["order_filters"]),
        )

    def filter_states(self, session: UserSession) -> dict[OrderStatus, bool]:
        active = set(self._get_active_filter_values(session))
        return {status: status.value in active for status in _MY_ORDERS_FILTER_STATUSES}

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
            return [item.value for item in _MY_ORDERS_FILTER_STATUSES]
        values = [str(item) for item in raw if str(item) in {status.value for status in _MY_ORDERS_FILTER_STATUSES}]
        return values or [item.value for item in _MY_ORDERS_FILTER_STATUSES]

    def _get_query_statuses(self, session: UserSession) -> list[str]:
        return sorted(set(self._get_active_filter_values(session)))

    def _filters_summary_text(self, session: UserSession) -> str:
        active = set(self._get_active_filter_values(session))
        parts: list[str] = []
        for status in ORDER_FILTER_STATUSES:
            marker = order_filter_title(status)
            emoji = "🟢" if status.value in active else "🔴"
            parts.append(f"{emoji} {marker}")
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

    async def _hydrate_preferences(self, session: UserSession, *, persist: bool = True) -> None:
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
                prefs["order_filters"] = list(DEFAULT_ORDER_FILTER_VALUES)
        merged = self._merge_preferences(session.state_data, prefs)
        merged["_prefs_loaded"] = True
        session.state_data = merged
        if persist:
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
    return order_status_title(status)


def _format_history_short(items: list[OrderStatusHistoryItem]) -> str:
    from app.services.order_list_format import format_order_history_short

    return format_order_history_short(items)


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


_MY_ORDERS_FILTER_STATUSES = ORDER_FILTER_STATUSES


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
