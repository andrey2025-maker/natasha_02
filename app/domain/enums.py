from __future__ import annotations

from enum import Enum


class Platform(str, Enum):
    TELEGRAM = "telegram"
    VK = "vk"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PRICE_READY = "price_ready"
    WAITING_PAYMENT = "waiting_payment"
    PAID_CHECK = "paid_check"
    PAID = "paid"
    IN_TRANSIT = "in_transit"
    PICKUP_POINT = "pickup_point"
    ISSUED = "issued"
    CANCELLED = "cancelled"


class DeliveryFlowType(str, Enum):
    BUYOUT = "buyout"
    SELF_BUYOUT = "self_buyout"


class SyncState(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    FAILED = "failed"


class DialogState(str, Enum):
    IDLE = "idle"
    PROFILE_FILL_NAME = "profile_fill_name"
    PROFILE_FILL_PHONE = "profile_fill_phone"
    PROFILE_FILL_CITY = "profile_fill_city"
    PROFILE_CONFIRM = "profile_confirm"
    PROFILE_EDIT_NAME = "profile_edit_name"
    PROFILE_EDIT_PHONE = "profile_edit_phone"
    PROFILE_EDIT_CITY = "profile_edit_city"
    PROFILE_ASK_HAS_CODE = "profile_ask_has_code"
    PROFILE_ENTER_CODE = "profile_enter_code"
    PROFILE_CONFIRM_CODE = "profile_confirm_code"
    PROFILE_ASK_PASSPORT = "profile_ask_passport"
    SYNC_ENTER_CODE = "sync_enter_code"
    SYNC_VERIFY = "sync_verify"
    BUYOUT_WAIT_MEDIA = "buyout_wait_media"
    BUYOUT_WAIT_LINK = "buyout_wait_link"
    BUYOUT_WAIT_DETAILS = "buyout_wait_details"
    BUYOUT_ADD_MORE = "buyout_add_more"
    TRACK_WAIT_INPUT = "track_wait_input"
    TRACK_WAIT_CONTINUE = "track_wait_continue"


class OutboundMessageStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
