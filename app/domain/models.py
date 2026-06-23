from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.domain.enums import (
    DeliveryFlowType,
    DialogState,
    OrderStatus,
    OutboundMessageStatus,
    Platform,
    SyncState,
)

DEFAULT_PROFILE_PRICE_PER_KG_RUB = 560


@dataclass(slots=True)
class UserProfile:
    id: int
    code: str
    name: str
    phone: str
    city: str
    has_passport: bool
    price_per_kg_rub: int = DEFAULT_PROFILE_PRICE_PER_KG_RUB
    telegram_user_id: Optional[int] = None
    vk_user_id: Optional[int] = None
    is_blocked_by_admin: bool = False
    blocked_bot: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_filled(self) -> bool:
        return bool(
            (self.name or "").strip()
            and (self.phone or "").strip()
            and (self.city or "").strip()
            and (self.code or "").strip()
        )


@dataclass(slots=True)
class BuyoutOrder:
    id: int
    user_profile_id: int
    order_number: str
    flow_type: DeliveryFlowType
    status: OrderStatus
    product_url: str
    quantity_text: str
    media_group_id: Optional[str] = None
    media_storage_chat_id: Optional[int] = None
    media_storage_topic_id: Optional[int] = None
    media_storage_message_id: Optional[int] = None
    media_vk_attachment: Optional[str] = None
    price_rub: Optional[int] = None
    track_number: Optional[str] = None
    manager_comment: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(slots=True)
class UserSession:
    id: int
    platform: Platform
    platform_user_id: int
    state: DialogState
    state_data: dict = field(default_factory=dict)
    user_profile_id: Optional[int] = None
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(slots=True)
class SyncRequest:
    id: int
    profile_code: str
    from_platform: Platform
    to_platform: Platform
    verification_code: str
    state: SyncState
    expires_at: datetime
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(slots=True)
class OutboundMessage:
    id: int
    platform: Platform
    platform_user_id: int
    message_type: str
    payload: dict
    status: OutboundMessageStatus = OutboundMessageStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(slots=True)
class FaqSection:
    id: int
    title: str
    parent_id: int | None = None
    content_text: str = ""
    sort_order: int = 0
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(slots=True)
class OrderStatusHistoryItem:
    id: int
    order_id: int
    previous_status: OrderStatus | None
    new_status: OrderStatus
    changed_by_platform: Platform
    changed_by_user_id: int
    note: str = ""
    changed_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(slots=True)
class OrderMediaItem:
    id: int
    order_id: int
    platform: Platform
    media_type: str
    tg_chat_id: int | None = None
    tg_topic_id: int | None = None
    tg_message_id: int | None = None
    tg_file_id: str | None = None
    vk_attachment: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
