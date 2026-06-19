from __future__ import annotations

import asyncio
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import asyncpg

from app.domain.enums import Platform
from app.domain.models import BuyoutOrder, UserProfile
from app.storage.interfaces import BuyoutOrderRepository, CodeReserveRepository, UserProfileRepository


_DB_POOLS: dict[str, asyncpg.Pool] = {}
_DB_POOLS_LOCK = asyncio.Lock()
_MEMORY_SETTINGS: dict[str, dict] = {}


@dataclass(slots=True)
class DbSettingsStore:
    database_dsn: str

    async def _pool(self) -> asyncpg.Pool:
        if self.database_dsn == "memory":
            raise RuntimeError("Pool is unavailable for memory backend")
        existing = _DB_POOLS.get(self.database_dsn)
        if existing is not None:
            return existing
        async with _DB_POOLS_LOCK:
            existing = _DB_POOLS.get(self.database_dsn)
            if existing is not None:
                return existing
            pool = await asyncpg.create_pool(dsn=self.database_dsn, min_size=1, max_size=4)
            _DB_POOLS[self.database_dsn] = pool
            return pool

    async def get(self, key: str) -> dict | None:
        if self.database_dsn == "memory":
            payload = _MEMORY_SETTINGS.get(key)
            return dict(payload) if isinstance(payload, dict) else None
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM app_settings WHERE key = $1", key)
        if not row:
            return None
        value = row["value"]
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                return None
            return dict(parsed) if isinstance(parsed, dict) else None
        return None

    async def set(self, key: str, value: dict) -> None:
        payload = dict(value)
        if self.database_dsn == "memory":
            _MEMORY_SETTINGS[key] = payload
            return
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES($1, $2::jsonb, NOW())
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = NOW()
                """,
                key,
                json.dumps(payload, ensure_ascii=False),
            )

    async def delete(self, key: str) -> None:
        if self.database_dsn == "memory":
            _MEMORY_SETTINGS.pop(key, None)
            return
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM app_settings WHERE key = $1", key)


def parse_codes(raw: str) -> list[str]:
    normalized = raw.replace("\n", ",").replace(";", ",")
    parts = [item.strip() for item in normalized.split(",")]
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        if not part:
            continue
        if not part.isdigit():
            continue
        code = part.zfill(3)
        if code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


_SUPPORTED_MEDIA_TYPES = {"photo", "video", "animation", "document"}


def _normalize_media_item(payload: dict) -> dict | None:
    media_type = str(payload.get("media_type", "")).strip()
    file_id = str(payload.get("file_id", "")).strip()
    if media_type not in _SUPPORTED_MEDIA_TYPES or not file_id:
        return None
    vk_attachment = str(payload.get("vk_attachment", "")).strip()
    return {
        "media_type": media_type,
        "file_id": file_id,
        "caption": str(payload.get("caption", "")),
        "vk_attachment": vk_attachment or None,
        "storage_chat_id": payload.get("storage_chat_id"),
        "storage_topic_id": payload.get("storage_topic_id"),
        "storage_message_id": payload.get("storage_message_id"),
    }


def _decode_media_items(payload: dict | None) -> list[dict]:
    if not payload:
        return []
    raw_items: list[dict] = []
    if isinstance(payload.get("items"), list):
        raw_items = [item for item in payload["items"] if isinstance(item, dict)]
    elif isinstance(payload, dict):
        raw_items = [payload]
    result: list[dict] = []
    for raw in raw_items:
        normalized = _normalize_media_item(raw)
        if normalized:
            result.append(normalized)
    return result


@dataclass(slots=True)
class PaymentTextStore:
    database_dsn: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def get_text(self) -> str:
        payload = await self._db.get("payment_text")
        text = str(payload.get("text", "")).strip() if payload else ""
        if not text:
            return (
                "Инструкция по оплате пока не заполнена.\n"
                "Нажмите «Ред. оплата» и отправьте текст, который увидит клиент."
            )
        return text

    async def save_text(self, text: str) -> None:
        await self._db.set("payment_text", {"text": text.strip()})

    async def get_media_items(self) -> list[dict]:
        payload = await self._db.get("payment_media")
        return _decode_media_items(payload)

    async def get_media(self) -> dict | None:
        items = await self.get_media_items()
        return items[0] if items else None

    async def save_media(
        self,
        media_type: str,
        file_id: str,
        caption: str = "",
        vk_attachment: str | None = None,
        storage_chat_id: int | None = None,
        storage_topic_id: int | None = None,
        storage_message_id: int | None = None,
    ) -> None:
        items = await self.get_media_items()
        new_item = _normalize_media_item(
            {
                "media_type": media_type,
                "file_id": file_id,
                "caption": caption.strip(),
                "vk_attachment": (vk_attachment or "").strip(),
                "storage_chat_id": int(storage_chat_id) if storage_chat_id else None,
                "storage_topic_id": int(storage_topic_id) if storage_topic_id else None,
                "storage_message_id": int(storage_message_id) if storage_message_id else None,
            }
        )
        if not new_item:
            return
        items.append(new_item)
        await self._db.set("payment_media", {"items": items})

    async def clear_media(self) -> None:
        await self._db.delete("payment_media")

    async def remove_media_at(self, index: int) -> bool:
        items = await self.get_media_items()
        if index < 1 or index > len(items):
            return False
        items.pop(index - 1)
        if items:
            await self._db.set("payment_media", {"items": items})
        else:
            await self._db.delete("payment_media")
        return True


@dataclass(slots=True)
class PaymentReviewTargetStore:
    database_dsn: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def get_target(self) -> tuple[int | None, int | None]:
        payload = await self._db.get("payment_review_target")
        if not payload:
            return None, None
        chat_raw = payload.get("chat_id")
        topic_raw = payload.get("topic_id")
        chat_id = int(chat_raw) if isinstance(chat_raw, (int, str)) and str(chat_raw).strip() else None
        topic_id = int(topic_raw) if isinstance(topic_raw, (int, str)) and str(topic_raw).strip() else None
        return chat_id, topic_id

    async def set_target(self, chat_id: int, topic_id: int | None = None) -> None:
        await self._db.set(
            "payment_review_target",
            {"chat_id": int(chat_id), "topic_id": int(topic_id) if topic_id else None},
        )

    async def clear_target(self) -> None:
        await self._db.delete("payment_review_target")


@dataclass(slots=True)
class NotificationSettingsStore:
    database_dsn: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def get_settings(self) -> dict[str, bool]:
        default = {
            "quiet_mode": False,
            "bot_messages": False,
            "button_messages": False,
            "user_messages": False,
        }
        payload = await self._db.get("notification_settings")
        if not payload:
            return dict(default)
        result = dict(default)
        for key in default:
            result[key] = bool(payload.get(key, default[key]))
        return result

    async def toggle(self, key: str) -> dict[str, bool]:
        settings = await self.get_settings()
        if key not in settings:
            return settings
        settings[key] = not settings[key]
        # If quiet mode is turned off, detail toggles stay as-is.
        # If quiet mode is turned on and no channel selected, default to all.
        if key == "quiet_mode" and settings["quiet_mode"]:
            if not any(
                [
                    settings["bot_messages"],
                    settings["button_messages"],
                    settings["user_messages"],
                ]
            ):
                settings["bot_messages"] = True
                settings["button_messages"] = True
                settings["user_messages"] = True
        await self.save_settings(settings)
        return settings

    async def save_settings(self, settings: dict[str, bool]) -> None:
        await self._db.set(
            "notification_settings",
            {
                "quiet_mode": bool(settings.get("quiet_mode", False)),
                "bot_messages": bool(settings.get("bot_messages", False)),
                "button_messages": bool(settings.get("button_messages", False)),
                "user_messages": bool(settings.get("user_messages", False)),
            },
        )

    async def should_disable_notification(self, category: str) -> bool:
        settings = await self.get_settings()
        if not settings.get("quiet_mode", False):
            return False
        key_map = {
            "bot": "bot_messages",
            "button": "button_messages",
            "user": "user_messages",
        }
        key = key_map.get(category)
        if not key:
            return False
        return bool(settings.get(key, False))


@dataclass(slots=True)
class ProhibitedGoodsStore:
    database_dsn: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def get_text(self) -> str:
        payload = await self._db.get("prohibited_goods_text")
        text = str(payload.get("text", "")).strip() if payload else ""
        if not text:
            return (
                "Раздел запрещенных товаров пока не заполнен.\n"
                "Если есть вопросы, напишите в раздел «Вопросы»."
            )
        return text

    async def save_text(self, text: str) -> None:
        await self._db.set("prohibited_goods_text", {"text": text.strip()})

    async def get_media_items(self) -> list[dict]:
        payload = await self._db.get("prohibited_goods_media")
        return _decode_media_items(payload)

    async def get_media(self) -> dict | None:
        items = await self.get_media_items()
        return items[0] if items else None

    async def save_media(
        self,
        media_type: str,
        file_id: str,
        caption: str = "",
        vk_attachment: str | None = None,
        storage_chat_id: int | None = None,
        storage_topic_id: int | None = None,
        storage_message_id: int | None = None,
    ) -> None:
        items = await self.get_media_items()
        new_item = _normalize_media_item(
            {
                "media_type": media_type,
                "file_id": file_id,
                "caption": caption.strip(),
                "vk_attachment": (vk_attachment or "").strip(),
                "storage_chat_id": int(storage_chat_id) if storage_chat_id else None,
                "storage_topic_id": int(storage_topic_id) if storage_topic_id else None,
                "storage_message_id": int(storage_message_id) if storage_message_id else None,
            }
        )
        if not new_item:
            return
        items.append(new_item)
        await self._db.set("prohibited_goods_media", {"items": items})

    async def clear_media(self) -> None:
        await self._db.delete("prohibited_goods_media")

    async def remove_media_at(self, index: int) -> bool:
        items = await self.get_media_items()
        if index < 1 or index > len(items):
            return False
        items.pop(index - 1)
        if items:
            await self._db.set("prohibited_goods_media", {"items": items})
        else:
            await self._db.delete("prohibited_goods_media")
        return True


@dataclass(slots=True)
class StaticContentStore:
    database_dsn: str
    key: str
    default_text: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def get_text(self) -> str:
        payload = await self._db.get(f"{self.key}.text")
        text = str(payload.get("text", "")).strip() if payload else ""
        return text or self.default_text

    async def save_text(self, text: str) -> None:
        await self._db.set(f"{self.key}.text", {"text": text.strip()})

    async def get_media_items(self) -> list[dict]:
        payload = await self._db.get(f"{self.key}.media")
        return _decode_media_items(payload)

    async def get_media(self) -> dict | None:
        items = await self.get_media_items()
        return items[0] if items else None

    async def save_media(
        self,
        media_type: str,
        file_id: str,
        caption: str = "",
        vk_attachment: str | None = None,
        storage_chat_id: int | None = None,
        storage_topic_id: int | None = None,
        storage_message_id: int | None = None,
    ) -> None:
        items = await self.get_media_items()
        new_item = _normalize_media_item(
            {
                "media_type": media_type,
                "file_id": file_id,
                "caption": caption.strip(),
                "vk_attachment": (vk_attachment or "").strip(),
                "storage_chat_id": int(storage_chat_id) if storage_chat_id else None,
                "storage_topic_id": int(storage_topic_id) if storage_topic_id else None,
                "storage_message_id": int(storage_message_id) if storage_message_id else None,
            }
        )
        if not new_item:
            return
        items.append(new_item)
        await self._db.set(f"{self.key}.media", {"items": items})

    async def clear_media(self) -> None:
        await self._db.delete(f"{self.key}.media")

    async def remove_media_at(self, index: int) -> bool:
        items = await self.get_media_items()
        if index < 1 or index > len(items):
            return False
        items.pop(index - 1)
        if items:
            await self._db.set(f"{self.key}.media", {"items": items})
        else:
            await self._db.delete(f"{self.key}.media")
        return True


@dataclass(slots=True)
class AdminPanelAccessStore:
    database_dsn: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def is_open_for_all_admins(self) -> bool:
        payload = await self._db.get("admins_access")
        if not payload:
            return False
        return bool(payload.get("open_for_all_admins", False))

    async def set_open_for_all_admins(self, is_open: bool) -> None:
        await self._db.set("admins_access", {"open_for_all_admins": bool(is_open)})

    async def toggle(self) -> bool:
        current = await self.is_open_for_all_admins()
        new_value = not current
        await self.set_open_for_all_admins(new_value)
        return new_value


@dataclass(slots=True)
class BlockReasonStore:
    database_dsn: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def list_reasons(self) -> dict[str, str]:
        payload = await self._db.get("block_reasons")
        if not payload:
            return {}
        result: dict[str, str] = {}
        if isinstance(payload, dict):
            for key, value in payload.items():
                if isinstance(key, str) and isinstance(value, str) and value.strip():
                    result[key] = value.strip()
        return result

    async def get_reason(self, code: str) -> str | None:
        reasons = await self.list_reasons()
        value = reasons.get(code.strip())
        return value if value else None

    async def set_reason(self, code: str, reason: str) -> None:
        reasons = await self.list_reasons()
        normalized_code = code.strip()
        normalized_reason = reason.strip()
        if normalized_reason:
            reasons[normalized_code] = normalized_reason
        else:
            reasons.pop(normalized_code, None)
        await self._save(reasons)

    async def clear_reason(self, code: str) -> None:
        reasons = await self.list_reasons()
        reasons.pop(code.strip(), None)
        await self._save(reasons)

    async def _save(self, reasons: dict[str, str]) -> None:
        await self._db.set("block_reasons", reasons)


@dataclass(slots=True)
class AdminProfileCommentStore:
    database_dsn: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def list_comments(self) -> dict[str, str]:
        payload = await self._db.get("profile_comments")
        if not payload:
            return {}
        result: dict[str, str] = {}
        if isinstance(payload, dict):
            for key, value in payload.items():
                if isinstance(key, str) and isinstance(value, str):
                    text = value.strip()
                    if text:
                        result[key] = text
        return result

    async def get_comment(self, code: str) -> str | None:
        comments = await self.list_comments()
        value = comments.get(code.strip())
        return value if value else None

    async def set_comment(self, code: str, comment: str) -> None:
        comments = await self.list_comments()
        normalized_code = code.strip()
        normalized_comment = comment.strip()
        if normalized_comment:
            comments[normalized_code] = normalized_comment
        else:
            comments.pop(normalized_code, None)
        await self._save(comments)

    async def _save(self, comments: dict[str, str]) -> None:
        await self._db.set("profile_comments", comments)


@dataclass(slots=True)
class GroupTopicsStore:
    database_dsn: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def get(self) -> dict:
        payload = await self._db.get("group_topics")
        if not payload:
            return {}
        return payload if isinstance(payload, dict) else {}

    async def set_tg_chat_id(self, chat_id: int) -> None:
        data = await self.get()
        data["tg_chat_id"] = int(chat_id)
        await self._save(data)

    async def clear_tg(self) -> None:
        data = await self.get()
        for key in [
            "tg_chat_id",
            "tg_logs_topic_id",
            "tg_payment_topic_id",
            "tg_questions_topic_id",
            "tg_buyout_topic_id",
        ]:
            data.pop(key, None)
        await self._save(data)

    async def get_topic_name_parts(self) -> list[str]:
        data = await self.get()
        raw = data.get("topic_name_parts")
        if not isinstance(raw, list):
            return ["code", "name"]
        allowed = {"code", "name", "phone", "city"}
        parts = [str(item).strip().lower() for item in raw if str(item).strip().lower() in allowed]
        return parts or ["code", "name"]

    async def set_topic_name_parts(self, parts: list[str]) -> None:
        allowed = {"code", "name", "phone", "city"}
        normalized = [str(item).strip().lower() for item in parts if str(item).strip().lower() in allowed]
        if not normalized:
            normalized = ["code", "name"]
        data = await self.get()
        data["topic_name_parts"] = normalized
        await self._save(data)

    async def toggle_topic_name_part(self, part: str) -> list[str]:
        normalized_part = str(part).strip().lower()
        allowed = {"code", "name", "phone", "city"}
        if normalized_part not in allowed:
            return await self.get_topic_name_parts()
        current = await self.get_topic_name_parts()
        if normalized_part in current:
            current = [item for item in current if item != normalized_part]
        else:
            current.append(normalized_part)
        await self.set_topic_name_parts(current)
        return await self.get_topic_name_parts()

    async def set_tg_topics(
        self,
        logs_topic_id: int,
        payment_topic_id: int,
        questions_topic_id: int,
        buyout_topic_id: int | None = None,
    ) -> None:
        data = await self.get()
        data["tg_logs_topic_id"] = int(logs_topic_id)
        data["tg_payment_topic_id"] = int(payment_topic_id)
        data["tg_questions_topic_id"] = int(questions_topic_id)
        if buyout_topic_id is not None:
            data["tg_buyout_topic_id"] = int(buyout_topic_id)
        await self._save(data)

    async def get_tg_topic(self, kind: str) -> tuple[int | None, int | None]:
        data = await self.get()
        chat_id_raw = data.get("tg_chat_id")
        if not chat_id_raw:
            return None, None
        chat_id = int(chat_id_raw)
        key_map = {
            "logs": "tg_logs_topic_id",
            "payment": "tg_payment_topic_id",
            "questions": "tg_questions_topic_id",
            "buyout": "tg_buyout_topic_id",
        }
        topic_key = key_map.get(kind)
        if not topic_key:
            return chat_id, None
        topic_raw = data.get(topic_key)
        return chat_id, (int(topic_raw) if topic_raw else None)

    async def set_vk_logs_peer_id(self, peer_id: int) -> None:
        data = await self.get()
        data["vk_logs_peer_id"] = int(peer_id)
        await self._save(data)

    async def get_vk_logs_peer_id(self) -> int | None:
        data = await self.get()
        raw = data.get("vk_logs_peer_id")
        return int(raw) if raw else None

    async def _save(self, data: dict) -> None:
        await self._db.set("group_topics", data)


@dataclass(slots=True)
class QuestionsAlertStore:
    database_dsn: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def create(
        self,
        *,
        alert_token: str,
        questions_chat_id: int,
        dialog_chat_id: int,
        dialog_topic_id: int,
        dialog_message_id: int,
        platform_user_id: int,
    ) -> None:
        payload = await self._load()
        payload[alert_token] = {
            "questions_chat_id": int(questions_chat_id),
            "questions_message_id": None,
            "dialog_chat_id": int(dialog_chat_id),
            "dialog_topic_id": int(dialog_topic_id),
            "dialog_message_id": int(dialog_message_id),
            "platform_user_id": int(platform_user_id),
            "processed_at": None,
            "processed_by_name": None,
        }
        await self._save(payload)

    async def attach_questions_message(self, alert_token: str, questions_message_id: int) -> None:
        payload = await self._load()
        item = payload.get(alert_token)
        if not isinstance(item, dict):
            return
        item["questions_message_id"] = int(questions_message_id)
        payload[alert_token] = item
        await self._save(payload)

    async def get(self, alert_token: str) -> dict | None:
        payload = await self._load()
        item = payload.get(alert_token)
        return item if isinstance(item, dict) else None

    async def mark_processed(self, alert_token: str, processed_by_name: str, processed_at: str) -> dict | None:
        payload = await self._load()
        item = payload.get(alert_token)
        if not isinstance(item, dict):
            return None
        item["processed_at"] = processed_at
        item["processed_by_name"] = processed_by_name.strip()
        payload[alert_token] = item
        await self._save(payload)
        return item

    async def _load(self) -> dict:
        payload = await self._db.get("questions_alerts")
        if not isinstance(payload, dict):
            return {}
        if len(payload) > 1000:
            keys = sorted(payload.keys())
            for stale in keys[:-1000]:
                payload.pop(stale, None)
        return payload

    async def _save(self, payload: dict) -> None:
        await self._db.set("questions_alerts", payload)


@dataclass(slots=True)
class TopicDialogStore:
    database_dsn: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def get_user_topic(self, chat_id: int, platform: str, platform_user_id: int) -> int | None:
        payload = await self._db.get("topic_dialog_user_topics")
        if not payload:
            return None
        key = self._user_key(chat_id, platform, platform_user_id)
        raw = payload.get(key)
        if not raw:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    async def set_user_topic(self, chat_id: int, platform: str, platform_user_id: int, topic_id: int) -> None:
        payload = await self._db.get("topic_dialog_user_topics") or {}
        key = self._user_key(chat_id, platform, platform_user_id)
        payload[key] = int(topic_id)
        await self._db.set("topic_dialog_user_topics", payload)

    async def get_pinned_profile_message_id(
        self,
        chat_id: int,
        platform: str,
        platform_user_id: int,
    ) -> int | None:
        payload = await self._db.get("topic_dialog_pinned_profiles")
        if not isinstance(payload, dict):
            return None
        raw = payload.get(self._user_key(chat_id, platform, platform_user_id))
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    async def set_pinned_profile_message_id(
        self,
        chat_id: int,
        platform: str,
        platform_user_id: int,
        message_id: int,
    ) -> None:
        payload = await self._db.get("topic_dialog_pinned_profiles") or {}
        payload[self._user_key(chat_id, platform, platform_user_id)] = int(message_id)
        await self._db.set("topic_dialog_pinned_profiles", payload)

    async def bind_topic_message_to_user(
        self,
        chat_id: int,
        topic_id: int | None,
        topic_message_id: int,
        platform: str,
        platform_user_id: int,
    ) -> None:
        payload = await self._db.get("topic_dialog_links") or {}
        topic_key = self._topic_key(chat_id, topic_id)
        topic_payload = payload.get(topic_key)
        if not isinstance(topic_payload, dict):
            topic_payload = {}
        topic_payload[str(int(topic_message_id))] = {
            "platform": platform,
            "platform_user_id": int(platform_user_id),
        }
        # Keep links compact per topic.
        if len(topic_payload) > 500:
            keys = sorted(topic_payload.keys(), key=lambda item: int(item))
            for stale in keys[:-500]:
                topic_payload.pop(stale, None)
        payload[topic_key] = topic_payload
        await self._db.set("topic_dialog_links", payload)

    async def resolve_user_by_topic_message(
        self,
        chat_id: int,
        topic_id: int | None,
        topic_message_id: int,
    ) -> tuple[str, int] | None:
        payload = await self._db.get("topic_dialog_links")
        if not payload:
            return None
        topic_key = self._topic_key(chat_id, topic_id)
        topic_payload = payload.get(topic_key)
        if not isinstance(topic_payload, dict):
            return None
        raw = topic_payload.get(str(int(topic_message_id)))
        if not isinstance(raw, dict):
            return None
        platform = str(raw.get("platform", "")).strip().lower()
        user_raw = raw.get("platform_user_id")
        try:
            user_id = int(user_raw)
        except (TypeError, ValueError):
            return None
        if not platform:
            return None
        return platform, user_id

    async def resolve_user_by_topic(
        self,
        chat_id: int,
        topic_id: int | None,
    ) -> tuple[str, int] | None:
        payload = await self._db.get("topic_dialog_user_topics")
        if not isinstance(payload, dict):
            return None
        expected = int(topic_id) if topic_id else 0
        prefix = f"{int(chat_id)}:"
        for raw_key, raw_topic in payload.items():
            if not isinstance(raw_key, str):
                continue
            if not raw_key.startswith(prefix):
                continue
            try:
                mapped_topic = int(raw_topic)
            except (TypeError, ValueError):
                continue
            if mapped_topic != expected:
                continue
            parts = raw_key.split(":", maxsplit=2)
            if len(parts) != 3:
                continue
            platform = parts[1].strip().lower()
            try:
                platform_user_id = int(parts[2])
            except (TypeError, ValueError):
                continue
            if platform:
                return platform, platform_user_id
        return None

    @staticmethod
    def _topic_key(chat_id: int, topic_id: int | None) -> str:
        return f"{int(chat_id)}:{int(topic_id) if topic_id else 0}"

    @staticmethod
    def _user_key(chat_id: int, platform: str, platform_user_id: int) -> str:
        return f"{int(chat_id)}:{platform}:{int(platform_user_id)}"


@dataclass(slots=True)
class BuyoutQuoteDraftStore:
    database_dsn: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def get(self, order_number: str) -> dict | None:
        payload = await self._db.get(self._key(order_number))
        if not isinstance(payload, dict):
            return None
        return payload

    async def save(
        self,
        order_number: str,
        *,
        price_rub: int,
        manager_comment: str,
        manager_user_id: int,
        group_message_id: int | None = None,
    ) -> None:
        await self._db.set(
            self._key(order_number),
            {
                "price_rub": int(price_rub),
                "manager_comment": manager_comment.strip(),
                "manager_user_id": int(manager_user_id),
                "group_message_id": int(group_message_id) if group_message_id else None,
            },
        )

    async def clear(self, order_number: str) -> None:
        await self._db.delete(self._key(order_number))

    @staticmethod
    def _key(order_number: str) -> str:
        return f"buyout_quote:{order_number.strip()}"


@dataclass(slots=True)
class FaqMediaStore:
    database_dsn: str

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def get_media_items(self, section_id: int) -> list[dict]:
        payload = await self._db.get(self._key(section_id))
        return _decode_media_items(payload)

    async def save_media(
        self,
        section_id: int,
        media_type: str,
        file_id: str,
        caption: str = "",
        vk_attachment: str | None = None,
        storage_chat_id: int | None = None,
        storage_topic_id: int | None = None,
        storage_message_id: int | None = None,
    ) -> None:
        items = await self.get_media_items(section_id)
        new_item = _normalize_media_item(
            {
                "media_type": media_type,
                "file_id": file_id,
                "caption": caption.strip(),
                "vk_attachment": (vk_attachment or "").strip(),
                "storage_chat_id": int(storage_chat_id) if storage_chat_id else None,
                "storage_topic_id": int(storage_topic_id) if storage_topic_id else None,
                "storage_message_id": int(storage_message_id) if storage_message_id else None,
            }
        )
        if not new_item:
            return
        items.append(new_item)
        await self._db.set(self._key(section_id), {"items": items})

    async def clear_media(self, section_id: int) -> None:
        await self._db.delete(self._key(section_id))

    async def remove_media_at(self, section_id: int, index: int) -> bool:
        items = await self.get_media_items(section_id)
        if index < 1 or index > len(items):
            return False
        items.pop(index - 1)
        if items:
            await self._db.set(self._key(section_id), {"items": items})
        else:
            await self._db.delete(self._key(section_id))
        return True

    @staticmethod
    def _key(section_id: int) -> str:
        return f"faq_media:{int(section_id)}"


@dataclass(slots=True)
class BackupService:
    database_dsn: str
    profile_repo: UserProfileRepository
    buyout_repo: BuyoutOrderRepository

    @property
    def _backup_dir(self) -> Path:
        return Path("/tmp/cargo_omsk55_backups")

    @property
    def _db(self) -> DbSettingsStore:
        return DbSettingsStore(self.database_dsn)

    async def create_db_backup(self) -> Path:
        backup_dir = self._backup_dir
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        target = backup_dir / f"db_backup_{stamp}.sql"

        if self.database_dsn == "memory":
            target.write_text("-- DATABASE_URL=memory; SQL dump is unavailable.\n", encoding="utf-8")
            await self._prune_files("db_backup_*.sql", keep=4)
            return target

        process = await asyncio.create_subprocess_exec(
            "pg_dump",
            self.database_dsn,
            "-f",
            str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            error_file = backup_dir / f"db_backup_error_{stamp}.txt"
            error_file.write_text(
                "Не удалось выполнить pg_dump.\n\n" + stderr.decode("utf-8", errors="replace"),
                encoding="utf-8",
            )
            await self._prune_files("db_backup_error_*.txt", keep=4)
            return error_file
        await self._prune_files("db_backup_*.sql", keep=4)
        return target

    async def create_excel_backup(self) -> Path:
        backup_dir = self._backup_dir
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        target = backup_dir / f"excel_backup_{stamp}.csv"

        profiles = await self._all_profiles()
        orders = await self._all_orders()
        with target.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["Profiles"])
            writer.writerow(
                [
                    "code",
                    "name",
                    "phone",
                    "city",
                    "has_passport",
                    "telegram_user_id",
                    "vk_user_id",
                    "is_blocked_by_admin",
                    "blocked_bot",
                    "created_at",
                    "last_activity_at",
                ]
            )
            for profile in profiles:
                writer.writerow(
                    [
                        profile.code,
                        profile.name,
                        profile.phone,
                        profile.city,
                        "yes" if profile.has_passport else "no",
                        profile.telegram_user_id or "",
                        profile.vk_user_id or "",
                        "yes" if profile.is_blocked_by_admin else "no",
                        "yes" if profile.blocked_bot else "no",
                        profile.created_at.isoformat(),
                        profile.last_activity_at.isoformat(),
                    ]
                )
            writer.writerow([])
            writer.writerow(["Orders"])
            writer.writerow(
                [
                    "order_number",
                    "profile_id",
                    "status",
                    "url",
                    "details",
                    "price_rub",
                    "track_number",
                    "manager_comment",
                    "created_at",
                    "updated_at",
                ]
            )
            for order in orders:
                writer.writerow(
                    [
                        order.order_number,
                        order.user_profile_id,
                        order.status.value,
                        order.product_url,
                        order.quantity_text,
                        order.price_rub if order.price_rub is not None else "",
                        order.track_number or "",
                        order.manager_comment,
                        order.created_at.isoformat(),
                        order.updated_at.isoformat(),
                    ]
                )
        await self._prune_files("excel_backup_*.csv", keep=4)
        return target

    async def auto_backup_enabled(self) -> bool:
        payload = await self._db.get("backup_auto_enabled")
        if not payload:
            return True
        return bool(payload.get("enabled", True))

    async def set_auto_backup_enabled(self, enabled: bool) -> None:
        await self._db.set("backup_auto_enabled", {"enabled": bool(enabled)})

    async def get_backup_target(self) -> tuple[int | None, int | None]:
        payload = await self._db.get("backup_target")
        if not payload:
            return None, None
        chat_raw = payload.get("chat_id")
        topic_raw = payload.get("topic_id")
        chat_id = int(chat_raw) if isinstance(chat_raw, (int, str)) and str(chat_raw).strip() else None
        topic_id = int(topic_raw) if isinstance(topic_raw, (int, str)) and str(topic_raw).strip() else None
        return chat_id, topic_id

    async def set_backup_target(self, chat_id: int, topic_id: int | None = None) -> None:
        await self._db.set(
            "backup_target",
            {"chat_id": int(chat_id), "topic_id": int(topic_id) if topic_id else None},
        )

    async def clear_backup_target(self) -> None:
        await self._db.delete("backup_target")

    async def pick_profiles_for_broadcast(self, audience: str) -> list[UserProfile]:
        profiles = await self._all_profiles()
        now = datetime.utcnow()
        month_ago = now - timedelta(days=30)
        if audience == "all":
            return profiles
        if audience == "active":
            return [item for item in profiles if item.last_activity_at >= month_ago]
        if audience == "inactive":
            return [item for item in profiles if item.last_activity_at < month_ago]
        return []

    async def _all_profiles(self) -> list[UserProfile]:
        items: list[UserProfile] = []
        page = 1
        page_size = 200
        while True:
            chunk = await self.profile_repo.list_profiles(limit=page_size, offset=(page - 1) * page_size)
            if not chunk:
                break
            items.extend(chunk)
            if len(chunk) < page_size:
                break
            page += 1
        return items

    async def _all_orders(self) -> list[BuyoutOrder]:
        items: list[BuyoutOrder] = []
        page = 1
        page_size = 200
        while True:
            chunk = await self.buyout_repo.list_all_recent(limit=page_size, offset=(page - 1) * page_size)
            if not chunk:
                break
            items.extend(chunk)
            if len(chunk) < page_size:
                break
            page += 1
        return items

    async def _prune_files(self, pattern: str, keep: int) -> None:
        files = sorted(self._backup_dir.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
        for stale in files[max(0, keep) :]:
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                continue


def count_targets_for_platform(profiles: Iterable[UserProfile], platform: Platform) -> int:
    if platform == Platform.TELEGRAM:
        return sum(1 for profile in profiles if profile.telegram_user_id)
    if platform == Platform.VK:
        return sum(1 for profile in profiles if profile.vk_user_id)
    return 0


async def send_stored_media_to_telegram(bot, chat_id: int, media: dict) -> bool:
    source_chat_raw = media.get("storage_chat_id")
    source_message_raw = media.get("storage_message_id")
    try:
        source_chat_id = int(source_chat_raw) if source_chat_raw else None
        source_message_id = int(source_message_raw) if source_message_raw else None
    except (TypeError, ValueError):
        source_chat_id = None
        source_message_id = None
    if source_chat_id and source_message_id:
        try:
            await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
            )
            return True
        except Exception:
            pass

    media_type = str(media.get("media_type", ""))
    file_id = str(media.get("file_id", ""))
    caption = str(media.get("caption", "")).strip()
    if not file_id:
        return False
    if media_type == "photo":
        await bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption or None)
        return True
    if media_type == "video":
        await bot.send_video(chat_id=chat_id, video=file_id, caption=caption or None)
        return True
    if media_type == "animation":
        await bot.send_animation(chat_id=chat_id, animation=file_id, caption=caption or None)
        return True
    if media_type == "document":
        await bot.send_document(chat_id=chat_id, document=file_id, caption=caption or None)
        return True
    return False


async def run_periodic_backup_loop(
    backup_service: BackupService,
    bot,
    target_chat_id: int,
    target_thread_id: int | None = None,
    notification_settings_store: NotificationSettingsStore | None = None,
    interval_seconds: int = 4 * 60 * 60,
) -> None:
    settings_store = notification_settings_store or NotificationSettingsStore(backup_service.database_dsn)
    while True:
        try:
            enabled = await backup_service.auto_backup_enabled()
            if enabled:
                file_chat_id, file_topic_id = await backup_service.get_backup_target()
                effective_chat_id = file_chat_id or target_chat_id
                effective_topic_id = file_topic_id if file_chat_id else target_thread_id
                file_path = await backup_service.create_db_backup()
                from aiogram.types import FSInputFile

                await bot.send_document(
                    chat_id=effective_chat_id,
                    document=FSInputFile(str(file_path)),
                    caption=f"Периодический бэкап: {file_path.name}",
                    disable_notification=await settings_store.should_disable_notification("bot"),
                    message_thread_id=effective_topic_id,
                )
        except Exception:
            # Loop must keep running even if one backup cycle fails.
            pass
        await asyncio.sleep(max(300, interval_seconds))
