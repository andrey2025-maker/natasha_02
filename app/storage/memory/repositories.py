from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from app.domain.enums import DialogState, OutboundMessageStatus, Platform, SyncState
from app.domain.models import (
    BuyoutOrder,
    FaqSection,
    OrderMediaItem,
    OrderStatusHistoryItem,
    OutboundMessage,
    SyncRequest,
    UserProfile,
    UserSession,
)
from app.storage.interfaces import (
    AdminRepository,
    BuyoutOrderRepository,
    CodeReserveRepository,
    FaqRepository,
    OutboundMessageRepository,
    SessionRepository,
    SyncRepository,
    UserProfileRepository,
)


class InMemoryUserProfileRepository(UserProfileRepository):
    def __init__(self) -> None:
        self._profiles: dict[int, UserProfile] = {}
        self._by_code: dict[str, int] = {}
        self._reserved_codes: set[str] = set()
        self._next_id = 1

    async def get_by_id(self, profile_id: int) -> UserProfile | None:
        profile = self._profiles.get(profile_id)
        return deepcopy(profile) if profile else None

    async def get_by_code(self, code: str) -> UserProfile | None:
        profile_id = self._by_code.get(code)
        return await self.get_by_id(profile_id) if profile_id else None

    async def get_by_platform_user(self, platform: Platform, platform_user_id: int) -> UserProfile | None:
        for profile in self._profiles.values():
            if platform == Platform.TELEGRAM and profile.telegram_user_id == platform_user_id:
                return deepcopy(profile)
            if platform == Platform.VK and profile.vk_user_id == platform_user_id:
                return deepcopy(profile)
        return None

    async def save(self, profile: UserProfile) -> UserProfile:
        stored = deepcopy(profile)
        if not stored.id:
            stored.id = self._next_id
            self._next_id += 1
        self._profiles[stored.id] = stored
        self._by_code[stored.code] = stored.id
        return deepcopy(stored)

    async def next_available_code(self) -> str:
        for number in range(1, 10000):
            code = f"{number:03d}"
            if code not in self._by_code and code not in self._reserved_codes:
                return code
        raise RuntimeError("No available profile codes")

    async def is_code_reserved(self, code: str) -> bool:
        return code in self._reserved_codes

    async def is_code_taken(self, code: str) -> bool:
        return code in self._by_code

    async def list_profiles(self, limit: int = 9, offset: int = 0) -> list[UserProfile]:
        rows = list(self._profiles.values())
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return [deepcopy(row) for row in rows[offset : offset + limit]]

    async def count_all(self) -> int:
        return len(self._profiles)

    async def count_active(self) -> int:
        return sum(
            1
            for item in self._profiles.values()
            if not item.is_blocked_by_admin and not item.blocked_bot
        )

    async def count_blocked_by_admin(self) -> int:
        return sum(1 for item in self._profiles.values() if item.is_blocked_by_admin)

    async def count_blocked_bot(self) -> int:
        return sum(1 for item in self._profiles.values() if item.blocked_bot)

    async def weekly_registrations(self, weeks: int = 8) -> list[tuple[str, int]]:
        safe_weeks = max(1, min(52, weeks))
        buckets: dict[str, int] = {}
        for profile in self._profiles.values():
            week_key = profile.created_at.strftime("%Y-%W")
            buckets[week_key] = buckets.get(week_key, 0) + 1
        rows = sorted(buckets.items(), key=lambda item: item[0], reverse=True)
        return rows[:safe_weeks]


class InMemorySessionRepository(SessionRepository):
    def __init__(self) -> None:
        self._sessions: dict[tuple[str, int], UserSession] = {}
        self._next_id = 1

    async def get(self, platform: Platform, platform_user_id: int) -> UserSession | None:
        session = self._sessions.get((platform.value, platform_user_id))
        return deepcopy(session) if session else None

    async def save(self, session: UserSession) -> UserSession:
        stored = deepcopy(session)
        if not stored.id:
            stored.id = self._next_id
            self._next_id += 1
        stored.updated_at = datetime.utcnow()
        self._sessions[(stored.platform.value, stored.platform_user_id)] = stored
        return deepcopy(stored)


class InMemorySyncRepository(SyncRepository):
    def __init__(self) -> None:
        self._requests: dict[int, SyncRequest] = {}
        self._next_id = 1

    async def save_request(self, sync_request: SyncRequest) -> SyncRequest:
        stored = deepcopy(sync_request)
        stored.id = self._next_id
        self._next_id += 1
        self._requests[stored.id] = stored
        return deepcopy(stored)

    async def get_active_by_profile_code(self, profile_code: str) -> SyncRequest | None:
        now = datetime.utcnow()
        candidates = [
            req
            for req in self._requests.values()
            if req.profile_code == profile_code and req.state == SyncState.PENDING and req.expires_at > now
        ]
        if not candidates:
            return None
        return deepcopy(max(candidates, key=lambda item: item.created_at))

    async def count_failed_attempts_since(self, profile_code: str, since: datetime) -> int:
        return sum(
            1
            for req in self._requests.values()
            if req.profile_code == profile_code and req.state == SyncState.FAILED and req.created_at >= since
        )

    async def mark_failed(self, sync_request_id: int) -> None:
        self._requests[sync_request_id].state = SyncState.FAILED

    async def mark_confirmed(self, sync_request_id: int) -> None:
        self._requests[sync_request_id].state = SyncState.CONFIRMED


class InMemoryCodeReserveRepository(CodeReserveRepository):
    def __init__(self, profile_repo: InMemoryUserProfileRepository) -> None:
        self._profile_repo = profile_repo

    async def add_codes(self, codes: list[str]) -> list[str]:
        added: list[str] = []
        for code in codes:
            if await self._profile_repo.is_code_taken(code):
                continue
            if code not in self._profile_repo._reserved_codes:
                self._profile_repo._reserved_codes.add(code)
                added.append(code)
        return added

    async def remove_codes(self, codes: list[str]) -> list[str]:
        removed: list[str] = []
        for code in codes:
            if code in self._profile_repo._reserved_codes:
                self._profile_repo._reserved_codes.discard(code)
                removed.append(code)
        return removed

    async def list_reserved(self) -> list[str]:
        return sorted(self._profile_repo._reserved_codes)


class InMemoryBuyoutOrderRepository(BuyoutOrderRepository):
    def __init__(self) -> None:
        self._orders: dict[int, BuyoutOrder] = {}
        self._history: dict[int, list[OrderStatusHistoryItem]] = {}
        self._media: dict[int, list[OrderMediaItem]] = {}
        self._next_id = 1
        self._next_history_id = 1
        self._next_media_id = 1

    async def create(self, order: BuyoutOrder) -> BuyoutOrder:
        stored = deepcopy(order)
        stored.id = self._next_id
        self._next_id += 1
        self._orders[stored.id] = stored
        return deepcopy(stored)

    async def count_for_user(self, user_profile_id: int) -> int:
        return sum(1 for order in self._orders.values() if order.user_profile_id == user_profile_id)

    async def count_all(self, statuses: list[str] | None = None) -> int:
        rows = list(self._orders.values())
        if statuses:
            allowed = set(statuses)
            rows = [order for order in rows if order.status.value in allowed]
        return len(rows)

    async def list_all_recent(
        self,
        limit: int = 9,
        offset: int = 0,
        statuses: list[str] | None = None,
    ) -> list[BuyoutOrder]:
        rows = list(self._orders.values())
        if statuses:
            allowed = set(statuses)
            rows = [order for order in rows if order.status.value in allowed]
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return [deepcopy(row) for row in rows[offset : offset + limit]]

    async def list_for_user(
        self,
        user_profile_id: int,
        limit: int = 9,
        offset: int = 0,
        statuses: list[str] | None = None,
    ) -> list[BuyoutOrder]:
        rows = [order for order in self._orders.values() if order.user_profile_id == user_profile_id]
        if statuses:
            allowed = set(statuses)
            rows = [order for order in rows if order.status.value in allowed]
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return [deepcopy(row) for row in rows[offset : offset + limit]]

    async def get_by_order_number(self, order_number: str) -> BuyoutOrder | None:
        for order in self._orders.values():
            if order.order_number == order_number:
                return deepcopy(order)
        return None

    async def update(self, order: BuyoutOrder) -> BuyoutOrder:
        if order.id not in self._orders:
            raise ValueError("Order not found")
        self._orders[order.id] = deepcopy(order)
        return deepcopy(order)

    async def add_status_history(self, item: OrderStatusHistoryItem) -> OrderStatusHistoryItem:
        stored = deepcopy(item)
        stored.id = self._next_history_id
        self._next_history_id += 1
        self._history.setdefault(stored.order_id, []).append(stored)
        return deepcopy(stored)

    async def list_status_history(self, order_id: int, limit: int = 20) -> list[OrderStatusHistoryItem]:
        rows = self._history.get(order_id, [])
        rows_sorted = sorted(rows, key=lambda item: item.changed_at, reverse=True)
        return [deepcopy(row) for row in rows_sorted[:limit]]

    async def count_by_status(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for order in self._orders.values():
            key = order.status.value
            result[key] = result.get(key, 0) + 1
        return result

    async def add_order_media(self, item: OrderMediaItem) -> OrderMediaItem:
        stored = deepcopy(item)
        stored.id = self._next_media_id
        self._next_media_id += 1
        self._media.setdefault(stored.order_id, []).append(stored)
        return deepcopy(stored)

    async def list_order_media(self, order_id: int) -> list[OrderMediaItem]:
        return [deepcopy(row) for row in self._media.get(order_id, [])]


class InMemoryOutboundMessageRepository(OutboundMessageRepository):
    def __init__(self) -> None:
        self._messages: dict[int, OutboundMessage] = {}
        self._next_id = 1

    async def enqueue(self, message: OutboundMessage) -> OutboundMessage:
        stored = deepcopy(message)
        stored.id = self._next_id
        self._next_id += 1
        self._messages[stored.id] = stored
        return deepcopy(stored)

    async def claim_pending(self, platform: Platform, limit: int = 50) -> list[OutboundMessage]:
        rows = [
            msg
            for msg in self._messages.values()
            if msg.platform == platform and msg.status == OutboundMessageStatus.PENDING
        ]
        rows.sort(key=lambda item: item.created_at)
        return [deepcopy(row) for row in rows[:limit]]

    async def mark_sent(self, message_id: int) -> None:
        if message_id in self._messages:
            self._messages[message_id].status = OutboundMessageStatus.SENT

    async def mark_failed(self, message_id: int) -> None:
        if message_id in self._messages:
            self._messages[message_id].status = OutboundMessageStatus.FAILED


class InMemoryAdminRepository(AdminRepository):
    def __init__(self) -> None:
        self._admins: set[int] = set()

    async def is_admin(self, telegram_user_id: int) -> bool:
        return telegram_user_id in self._admins

    async def add_admin(self, telegram_user_id: int, added_by: int) -> None:
        _ = added_by
        self._admins.add(telegram_user_id)

    async def remove_admin(self, telegram_user_id: int) -> None:
        self._admins.discard(telegram_user_id)

    async def list_admins(self) -> list[int]:
        return sorted(self._admins)


class InMemoryFaqRepository(FaqRepository):
    def __init__(self) -> None:
        self._sections: dict[int, FaqSection] = {}
        self._next_id = 1

    async def list_children(self, parent_id: int | None) -> list[FaqSection]:
        rows = [
            deepcopy(item)
            for item in self._sections.values()
            if item.parent_id == parent_id and item.is_active
        ]
        rows.sort(key=lambda item: (item.sort_order, item.id))
        return rows

    async def get_by_id(self, section_id: int) -> FaqSection | None:
        item = self._sections.get(section_id)
        return deepcopy(item) if item else None

    async def create(self, section: FaqSection) -> FaqSection:
        stored = deepcopy(section)
        stored.id = self._next_id
        self._next_id += 1
        self._sections[stored.id] = stored
        return deepcopy(stored)

    async def update(self, section: FaqSection) -> FaqSection:
        if section.id not in self._sections:
            raise ValueError("FAQ section not found")
        self._sections[section.id] = deepcopy(section)
        return deepcopy(section)
