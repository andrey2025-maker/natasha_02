from __future__ import annotations

import re

from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import Message

from app.bot.telegram.handlers.admin.html import _h
from app.core.container import AppContainer
from app.domain.models import UserProfile
from app.services.admin_tools_service import (
    FaqMediaStore,
    PaymentTextStore,
    ProhibitedGoodsStore,
    StaticContentStore,
)

async def _mark_blocked_bot_if_needed(container: AppContainer, profile: UserProfile, error: Exception) -> None:
    # Telegram returns forbidden when user blocked bot or deactivated account.
    if isinstance(error, TelegramForbiddenError):
        if not profile.blocked_bot:
            profile.blocked_bot = True
            await container.profile_repo.save(profile)


_DELETE_MEDIA_RE = re.compile(r"^\s*удалить\s+медиа\s+(\d+)\s*$", re.IGNORECASE)


async def _handle_media_text_command(
    message: Message,
    store: PaymentTextStore | ProhibitedGoodsStore | StaticContentStore | FaqMediaStore,
    section_name: str,
    section_id: int | None = None,
) -> bool:
    text = (message.text or "").strip()
    if text.lower() in {"список медиа", "медиа список"}:
        items = await store.get_media_items(section_id) if section_id is not None else await store.get_media_items()
        await message.answer(
            f"Медиа {section_name}: {len(items)}\n{_media_items_summary(items)}\n"
            "Удаление: «Удалить медиа &lt;номер&gt;»."
        )
        return True
    match = _DELETE_MEDIA_RE.match(text)
    if not match:
        return False
    index = int(match.group(1))
    removed = await store.remove_media_at(section_id, index) if section_id is not None else await store.remove_media_at(index)
    if not removed:
        items = await store.get_media_items(section_id) if section_id is not None else await store.get_media_items()
        await message.answer(
            f"Не удалось удалить медиа #{index}. Сейчас файлов: {len(items)}.\n{_media_items_summary(items)}"
        )
        return True
    items = await store.get_media_items(section_id) if section_id is not None else await store.get_media_items()
    await message.answer(
        f"Медиа #{index} удалено из раздела {section_name}. Осталось: {len(items)}.\n{_media_items_summary(items)}"
    )
    return True


def _media_items_summary(items: list[dict], limit: int = 20) -> str:
    if not items:
        return "Список медиа пуст."
    lines = []
    for idx, item in enumerate(items[:limit], start=1):
        media_type = str(item.get("media_type", "unknown"))
        caption = str(item.get("caption", "")).strip()
        suffix = f" — {_h(caption[:40])}" if caption else ""
        lines.append(f"{idx}. {_h(media_type)}{suffix}")
    if len(items) > limit:
        lines.append(f"... и еще {len(items) - limit}")
    return "\n".join(lines)
