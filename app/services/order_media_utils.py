from __future__ import annotations

from app.domain.models import BuyoutOrder, OrderMediaItem

_SUPPORTED_MEDIA_TYPES = {"photo", "video", "animation", "document"}


def order_media_item_to_stored_dict(item: OrderMediaItem) -> dict | None:
    media_type = str(item.media_type or "").strip()
    file_id = str(item.tg_file_id or "").strip()
    storage_chat_id = int(item.tg_chat_id) if item.tg_chat_id else None
    storage_message_id = int(item.tg_message_id) if item.tg_message_id else None
    if storage_chat_id and storage_message_id:
        return {
            "media_type": media_type if media_type in _SUPPORTED_MEDIA_TYPES else "photo",
            "file_id": file_id,
            "storage_chat_id": storage_chat_id,
            "storage_message_id": storage_message_id,
        }
    if file_id and media_type in _SUPPORTED_MEDIA_TYPES:
        return {
            "media_type": media_type,
            "file_id": file_id,
            "storage_chat_id": None,
            "storage_message_id": None,
        }
    return None


def collect_order_media_dicts(order: BuyoutOrder, items: list[OrderMediaItem]) -> list[dict]:
    result: list[dict] = []
    seen: set[tuple[int | None, int | None, str]] = set()
    for item in items:
        media = order_media_item_to_stored_dict(item)
        if not media:
            continue
        key = (
            media.get("storage_chat_id"),
            media.get("storage_message_id"),
            str(media.get("file_id", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(media)
    if not result and order.media_storage_chat_id and order.media_storage_message_id:
        result.append(
            {
                "media_type": "photo",
                "file_id": "",
                "storage_chat_id": int(order.media_storage_chat_id),
                "storage_message_id": int(order.media_storage_message_id),
            }
        )
    return result
