from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import OrderStatus
from app.storage.interfaces import BuyoutOrderRepository, UserProfileRepository


@dataclass(slots=True)
class StatsService:
    profile_repo: UserProfileRepository
    buyout_repo: BuyoutOrderRepository

    async def build_overview_text(self) -> str:
        total_users = await self.profile_repo.count_all()
        active_users = await self.profile_repo.count_active()
        blocked_by_admin = await self.profile_repo.count_blocked_by_admin()
        blocked_bot = await self.profile_repo.count_blocked_bot()
        order_status_counts = await self.buyout_repo.count_by_status()
        weekly = await self.profile_repo.weekly_registrations(weeks=8)

        def pct(value: int) -> str:
            if total_users <= 0:
                return "0.0%"
            return f"{(value / total_users) * 100:.1f}%"

        lines = [
            "<b>Статистика</b>",
            "",
            f"Всего пользователей: <b>{total_users}</b>",
            f"Активные: <b>{active_users}</b> ({pct(active_users)})",
            f"Заблокированные админом: <b>{blocked_by_admin}</b> ({pct(blocked_by_admin)})",
            f"Отписанные/заблокировали бота: <b>{blocked_bot}</b> ({pct(blocked_bot)})",
            "",
            "<b>Статусы заказов (выкуп):</b>",
        ]

        if order_status_counts:
            for status in OrderStatus:
                count = order_status_counts.get(status.value, 0)
                lines.append(f"- {_order_status_name(status)}: {count}")
        else:
            lines.append("- Пока нет заказов")

        lines.append("")
        lines.append("<b>Регистрации по неделям:</b>")
        if weekly:
            for bucket, count in weekly:
                lines.append(f"- {bucket}: {count}")
        else:
            lines.append("- Пока нет данных")

        return "\n".join(lines)


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
