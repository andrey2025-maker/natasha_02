from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.handlers.admin.media_helpers import _mark_blocked_bot_if_needed
from app.core.container import AppContainer
from app.domain.enums import Platform
from app.domain.models import OutboundMessage, UserProfile
from app.services.admin_tools_service import BackupService

async def _resolve_broadcast_profiles(
    backup_service: BackupService,
    audience: str,
    target_codes: list[str] | None = None,
) -> list[UserProfile]:
    if audience == "codes":
        return await backup_service.pick_profiles_by_codes(list(target_codes or []))
    return await backup_service.pick_profiles_for_broadcast(audience)


async def _dispatch_broadcast_text(
    message: Message,
    container: AppContainer,
    profiles: list[UserProfile],
    text: str,
) -> tuple[int, int, int]:
    tg_sent = 0
    tg_failed = 0
    vk_enqueued = 0
    for profile in profiles:
        if profile.telegram_user_id:
            try:
                await message.bot.send_message(chat_id=profile.telegram_user_id, text=text, parse_mode=None)
                tg_sent += 1
            except Exception as exc:
                tg_failed += 1
                await _mark_blocked_bot_if_needed(container, profile, exc)
        if profile.vk_user_id:
            await container.outbound_repo.enqueue(
                OutboundMessage(
                    id=0,
                    platform=Platform.VK,
                    platform_user_id=int(profile.vk_user_id),
                    message_type="broadcast_text",
                    payload={"text": text},
                )
            )
            vk_enqueued += 1
    return tg_sent, tg_failed, vk_enqueued


async def _dispatch_broadcast_media(
    message: Message,
    container: AppContainer,
    profiles: list[UserProfile],
) -> tuple[int, int, int]:
    caption = message.caption or ""
    media_kind = ""
    media_id = ""
    if message.photo:
        media_kind = "photo"
        media_id = message.photo[-1].file_id
    elif message.video:
        media_kind = "video"
        media_id = message.video.file_id
    elif message.animation:
        media_kind = "animation"
        media_id = message.animation.file_id
    elif message.document:
        media_kind = "document"
        media_id = message.document.file_id
    tg_sent = 0
    tg_failed = 0
    vk_enqueued = 0
    for profile in profiles:
        if profile.telegram_user_id:
            try:
                if media_kind == "photo":
                    await message.bot.send_photo(
                        chat_id=profile.telegram_user_id,
                        photo=media_id,
                        caption=caption,
                        parse_mode=None,
                    )
                elif media_kind == "video":
                    await message.bot.send_video(
                        chat_id=profile.telegram_user_id,
                        video=media_id,
                        caption=caption,
                        parse_mode=None,
                    )
                elif media_kind == "animation":
                    await message.bot.send_animation(
                        chat_id=profile.telegram_user_id,
                        animation=media_id,
                        caption=caption,
                        parse_mode=None,
                    )
                elif media_kind == "document":
                    await message.bot.send_document(
                        chat_id=profile.telegram_user_id,
                        document=media_id,
                        caption=caption,
                        parse_mode=None,
                    )
                tg_sent += 1
            except Exception as exc:
                tg_failed += 1
                await _mark_blocked_bot_if_needed(container, profile, exc)
        if profile.vk_user_id:
            fallback_text = (
                "Админ отправил медиа-рассылку.\n"
                "В текущей версии для VK доставляется текстовый вариант.\n"
                + (f"\n{caption}" if caption else "")
            )
            await container.outbound_repo.enqueue(
                OutboundMessage(
                    id=0,
                    platform=Platform.VK,
                    platform_user_id=int(profile.vk_user_id),
                    message_type="plain_text",
                    payload={"text": fallback_text},
                )
            )
            vk_enqueued += 1
    return tg_sent, tg_failed, vk_enqueued

def _broadcast_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Всем",
                    callback_data=codec.encode("admin:broadcast:all", user_id),
                ),
                InlineKeyboardButton(
                    text="Активные",
                    callback_data=codec.encode("admin:broadcast:active", user_id),
                ),
                InlineKeyboardButton(
                    text="Не активные",
                    callback_data=codec.encode("admin:broadcast:inactive", user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="По кодам",
                    callback_data=codec.encode("admin:broadcast:codes", user_id),
                ),
            ],
        ]
    )
