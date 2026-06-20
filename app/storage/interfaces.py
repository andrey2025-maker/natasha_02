from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from app.domain.enums import Platform
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


class UserProfileRepository(ABC):
    @abstractmethod
    async def get_by_id(self, profile_id: int) -> Optional[UserProfile]:
        raise NotImplementedError

    @abstractmethod
    async def get_by_code(self, code: str) -> Optional[UserProfile]:
        raise NotImplementedError

    @abstractmethod
    async def get_by_platform_user(self, platform: Platform, platform_user_id: int) -> Optional[UserProfile]:
        raise NotImplementedError

    @abstractmethod
    async def save(self, profile: UserProfile) -> UserProfile:
        raise NotImplementedError

    @abstractmethod
    async def next_available_code(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def is_code_reserved(self, code: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def is_code_taken(self, code: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def list_profiles(self, limit: int = 9, offset: int = 0) -> list[UserProfile]:
        raise NotImplementedError

    @abstractmethod
    async def count_all(self) -> int:
        raise NotImplementedError

    @abstractmethod
    async def count_active(self) -> int:
        raise NotImplementedError

    @abstractmethod
    async def count_blocked_by_admin(self) -> int:
        raise NotImplementedError

    @abstractmethod
    async def count_blocked_bot(self) -> int:
        raise NotImplementedError

    @abstractmethod
    async def weekly_registrations(self, weeks: int = 8) -> list[tuple[str, int]]:
        raise NotImplementedError


class SyncRepository(ABC):
    @abstractmethod
    async def save_request(self, sync_request: SyncRequest) -> SyncRequest:
        raise NotImplementedError

    @abstractmethod
    async def get_active_by_profile_code(self, profile_code: str) -> Optional[SyncRequest]:
        raise NotImplementedError

    @abstractmethod
    async def count_failed_attempts_since(self, profile_code: str, since: datetime) -> int:
        raise NotImplementedError

    @abstractmethod
    async def mark_failed(self, sync_request_id: int) -> None:
        raise NotImplementedError

    @abstractmethod
    async def mark_confirmed(self, sync_request_id: int) -> None:
        raise NotImplementedError

    @abstractmethod
    async def rename_active_profile_code(self, old_code: str, new_code: str) -> int:
        raise NotImplementedError


class SessionRepository(ABC):
    @abstractmethod
    async def get(self, platform: Platform, platform_user_id: int) -> Optional[UserSession]:
        raise NotImplementedError

    @abstractmethod
    async def save(self, session: UserSession) -> UserSession:
        raise NotImplementedError


class CodeReserveRepository(ABC):
    @abstractmethod
    async def add_codes(self, codes: list[str]) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    async def remove_codes(self, codes: list[str]) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    async def list_reserved(self) -> list[str]:
        raise NotImplementedError


class BuyoutOrderRepository(ABC):
    @abstractmethod
    async def create(self, order: BuyoutOrder) -> BuyoutOrder:
        raise NotImplementedError

    @abstractmethod
    async def count_for_user(self, user_profile_id: int) -> int:
        raise NotImplementedError

    @abstractmethod
    async def list_for_user(
        self,
        user_profile_id: int,
        limit: int = 9,
        offset: int = 0,
        statuses: list[str] | None = None,
    ) -> list[BuyoutOrder]:
        raise NotImplementedError

    @abstractmethod
    async def count_all(self, statuses: list[str] | None = None) -> int:
        raise NotImplementedError

    @abstractmethod
    async def list_all_recent(
        self,
        limit: int = 9,
        offset: int = 0,
        statuses: list[str] | None = None,
    ) -> list[BuyoutOrder]:
        raise NotImplementedError

    @abstractmethod
    async def get_by_order_number(self, order_number: str) -> BuyoutOrder | None:
        raise NotImplementedError

    @abstractmethod
    async def update(self, order: BuyoutOrder) -> BuyoutOrder:
        raise NotImplementedError

    @abstractmethod
    async def add_status_history(self, item: OrderStatusHistoryItem) -> OrderStatusHistoryItem:
        raise NotImplementedError

    @abstractmethod
    async def list_status_history(self, order_id: int, limit: int = 20) -> list[OrderStatusHistoryItem]:
        raise NotImplementedError

    @abstractmethod
    async def count_by_status(self) -> dict[str, int]:
        raise NotImplementedError

    @abstractmethod
    async def add_order_media(self, item: OrderMediaItem) -> OrderMediaItem:
        raise NotImplementedError

    @abstractmethod
    async def list_order_media(self, order_id: int) -> list[OrderMediaItem]:
        raise NotImplementedError

    @abstractmethod
    async def search_orders(self, by: str, query: str, limit: int = 30) -> list[BuyoutOrder]:
        raise NotImplementedError


class OutboundMessageRepository(ABC):
    @abstractmethod
    async def enqueue(self, message: OutboundMessage) -> OutboundMessage:
        raise NotImplementedError

    @abstractmethod
    async def claim_pending(self, platform: Platform, limit: int = 50) -> list[OutboundMessage]:
        raise NotImplementedError

    @abstractmethod
    async def mark_sent(self, message_id: int) -> None:
        raise NotImplementedError

    @abstractmethod
    async def mark_failed(self, message_id: int) -> None:
        raise NotImplementedError


class AdminRepository(ABC):
    @abstractmethod
    async def is_admin(self, telegram_user_id: int) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def add_admin(self, telegram_user_id: int, added_by: int) -> None:
        raise NotImplementedError

    @abstractmethod
    async def remove_admin(self, telegram_user_id: int) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_admins(self) -> list[int]:
        raise NotImplementedError


class FaqRepository(ABC):
    @abstractmethod
    async def list_children(self, parent_id: int | None) -> list[FaqSection]:
        raise NotImplementedError

    @abstractmethod
    async def get_by_id(self, section_id: int) -> FaqSection | None:
        raise NotImplementedError

    @abstractmethod
    async def create(self, section: FaqSection) -> FaqSection:
        raise NotImplementedError

    @abstractmethod
    async def update(self, section: FaqSection) -> FaqSection:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, section_id: int) -> bool:
        raise NotImplementedError
