from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import OrderStatus, Platform
from app.domain.models import BuyoutOrder, OrderStatusHistoryItem
from app.storage.interfaces import BuyoutOrderRepository, UserProfileRepository


@dataclass(slots=True)
class OrderAdminService:
    repository: BuyoutOrderRepository
    profile_repo: UserProfileRepository | None = None

    async def set_status(
        self,
        order_number: str,
        new_status: OrderStatus,
        changed_by_user_id: int,
        note: str = "",
        platform: Platform = Platform.TELEGRAM,
    ) -> BuyoutOrder | None:
        order = await self.repository.get_by_order_number(order_number.strip())
        if not order:
            return None
        previous = order.status
        if previous == new_status and not note:
            return order
        order.status = new_status
        order.updated_at = datetime.utcnow()
        order = await self.repository.update(order)
        await self.repository.add_status_history(
            OrderStatusHistoryItem(
                id=0,
                order_id=order.id,
                previous_status=previous,
                new_status=new_status,
                changed_by_platform=platform,
                changed_by_user_id=changed_by_user_id,
                note=note.strip(),
                changed_at=datetime.utcnow(),
            )
        )
        return order

    async def history(self, order_id: int, limit: int = 20) -> list[OrderStatusHistoryItem]:
        return await self.repository.list_status_history(order_id, limit=limit)

    async def get_order(self, order_number: str) -> BuyoutOrder | None:
        return await self.repository.get_by_order_number(order_number.strip())

    async def list_recent_orders(
        self,
        page: int = 1,
        page_size: int = 9,
        statuses: list[OrderStatus] | None = None,
    ) -> tuple[list[BuyoutOrder], int]:
        safe_page = max(1, page)
        status_values = [item.value for item in statuses] if statuses else None
        total = await self.repository.count_all(statuses=status_values)
        offset = (safe_page - 1) * page_size
        items = await self.repository.list_all_recent(limit=page_size, offset=offset, statuses=status_values)
        return items, total

    async def search_orders(self, by: str, query: str, limit: int = 30) -> list[BuyoutOrder]:
        mode = by.strip().lower()
        needle = query.strip()
        if not needle:
            return []
        if mode == "code" and self.profile_repo is not None:
            code = needle.zfill(3) if needle.isdigit() else needle
            profile = await self.profile_repo.get_by_code(code)
            if profile:
                return await self.repository.list_for_user(profile.id, limit=limit, offset=0)
        return await self.repository.search_orders(mode, query, limit=limit)

    async def update_order_field(self, order_number: str, field_name: str, raw_value: str) -> BuyoutOrder | None:
        order = await self.repository.get_by_order_number(order_number.strip())
        if not order:
            return None

        value = raw_value.strip()
        if field_name == "product_url":
            order.product_url = value
        elif field_name == "quantity_text":
            order.quantity_text = value
        elif field_name == "manager_comment":
            order.manager_comment = value
        elif field_name == "price_rub":
            if not value:
                order.price_rub = None
            else:
                parsed = int(value.replace(" ", ""))
                order.price_rub = parsed
        elif field_name == "track_number":
            order.track_number = value or None
        else:
            raise ValueError("Unsupported field")

        order.updated_at = datetime.utcnow()
        return await self.repository.update(order)

    async def bulk_update_field(self, order_numbers: list[str], field_name: str, raw_value: str) -> int:
        changed = 0
        for order_number in order_numbers:
            updated = await self.update_order_field(order_number, field_name, raw_value)
            if updated:
                changed += 1
        return changed
