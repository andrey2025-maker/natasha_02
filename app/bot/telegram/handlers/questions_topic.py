from __future__ import annotations

import secrets
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.telegram.callbacks import CallbackCodec
from app.core.container import AppContainer
from app.domain.enums import Platform
from app.services.admin_tools_service import (
    GroupTopicsStore,
    NotificationSettingsStore,
    QuestionsAlertStore,
    TopicDialogStore,
)


def build_tg_forum_message_link(chat_id: int, message_id: int, topic_id: int | None) -> str:
    internal_chat_id = str(int(chat_id)).removeprefix("-100")
    link = f"https://t.me/c/{internal_chat_id}/{int(message_id)}"
    if topic_id:
        link += f"?thread={int(topic_id)}"
    return link


def format_omsk_now() -> str:
    return datetime.now(ZoneInfo("Asia/Omsk")).strftime("%d.%m.%Y %H:%M")


def processor_display_name(user) -> str:
    username = (getattr(user, "username", None) or "").strip()
    if username:
        return f"@{username}"
    full_name = " ".join(
        part
        for part in (
            getattr(user, "first_name", None),
            getattr(user, "last_name", None),
        )
        if part
    ).strip()
    return escape(full_name or str(getattr(user, "id", "unknown")), quote=False)


async def forward_idle_message_to_questions_topic(
    message: Message,
    *,
    container: AppContainer,
    group_topics_store: GroupTopicsStore,
    notification_settings_store: NotificationSettingsStore,
    topic_dialog_store: TopicDialogStore,
    questions_alert_store: QuestionsAlertStore,
    callback_codec: CallbackCodec,
    send_ack: bool = True,
) -> None:
    if not message.from_user:
        return

    profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, message.from_user.id)
    logs_chat_id, logs_default_topic_id = await group_topics_store.get_tg_topic("logs")
    questions_chat_id, questions_topic_id = await group_topics_store.get_tg_topic("questions")
    if not logs_chat_id or not questions_chat_id or not questions_topic_id:
        if send_ack:
            await message.answer("Передал вопрос менеджеру. Ответим в этом чате как можно скорее.")
        return

    dialog_topic_id = await resolve_or_create_user_topic(
        message=message,
        target_chat_id=int(logs_chat_id),
        default_topic_id=logs_default_topic_id,
        group_topics_store=group_topics_store,
        topic_dialog_store=topic_dialog_store,
        profile=profile,
    )
    if not dialog_topic_id:
        return

    disable_notification = await notification_settings_store.should_disable_notification("user")
    try:
        dialog_copy = await message.bot.copy_message(
            chat_id=int(logs_chat_id),
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=int(dialog_topic_id),
            disable_notification=disable_notification,
        )
    except Exception:
        return

    await topic_dialog_store.bind_topic_message_to_user(
        chat_id=int(logs_chat_id),
        topic_id=int(dialog_topic_id),
        topic_message_id=int(dialog_copy.message_id),
        platform=Platform.TELEGRAM.value,
        platform_user_id=message.from_user.id,
    )

    dialog_link = build_tg_forum_message_link(
        chat_id=int(logs_chat_id),
        message_id=int(dialog_copy.message_id),
        topic_id=int(dialog_topic_id),
    )

    profile_hint = "без профиля"
    if profile:
        profile_hint = f"{profile.code} / {profile.name or 'без имени'}"
    body = (message.text or message.caption or "").strip()
    safe_profile_hint = escape(profile_hint, quote=False)
    alert_text = (
        "📩 <b>Вопрос от клиента</b>\n"
        f"Профиль: <b>{safe_profile_hint}</b>\n"
        f"TG ID: <code>{message.from_user.id}</code>\n\n"
    )
    if body:
        alert_text += f"{escape(body, quote=False)}\n\n"
    elif not message.text:
        alert_text += "Медиа-сообщение клиента ниже.\n\n"
    alert_text += f'🔗 <a href="{dialog_link}">Открыть в диалоге</a>'

    alert_token = secrets.token_urlsafe(6)[:10]
    await questions_alert_store.create(
        alert_token=alert_token,
        questions_chat_id=int(questions_chat_id),
        dialog_chat_id=int(logs_chat_id),
        dialog_topic_id=int(dialog_topic_id),
        dialog_message_id=int(dialog_copy.message_id),
        platform_user_id=message.from_user.id,
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔴 Обработать",
                    callback_data=callback_codec.encode_public(f"questions:process:{alert_token}"),
                )
            ]
        ]
    )

    try:
        alert_message = await message.bot.send_message(
            chat_id=int(questions_chat_id),
            text=alert_text,
            parse_mode="HTML",
            message_thread_id=int(questions_topic_id),
            reply_markup=keyboard,
            disable_notification=disable_notification,
        )
        await questions_alert_store.attach_questions_message(alert_token, int(alert_message.message_id))
        if not message.text:
            await message.bot.copy_message(
                chat_id=int(questions_chat_id),
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=int(questions_topic_id),
                reply_to_message_id=int(alert_message.message_id),
                disable_notification=True,
            )
    except Exception:
        return

    if send_ack:
        await message.answer("Передал вопрос менеджеру. Ответим в этом чате как можно скорее.")


async def resolve_or_create_user_topic(
    message: Message,
    target_chat_id: int,
    default_topic_id: int | None,
    group_topics_store: GroupTopicsStore,
    topic_dialog_store: TopicDialogStore,
    profile,
) -> int | None:
    existing = await topic_dialog_store.get_user_topic(
        chat_id=target_chat_id,
        platform=Platform.TELEGRAM.value,
        platform_user_id=message.from_user.id if message.from_user else 0,
    )
    if existing:
        return existing
    profile_code = profile.code if profile else "—"
    profile_name = (profile.name if profile else "") or "без имени"
    profile_phone = (profile.phone if profile else "") or "без телефона"
    profile_city = (profile.city if profile else "") or "без города"
    selected = await group_topics_store.get_topic_name_parts()
    parts: list[str] = []
    if "code" in selected:
        parts.append(str(profile_code))
    if "name" in selected:
        parts.append(str(profile_name))
    if "phone" in selected:
        parts.append(str(profile_phone))
    if "city" in selected:
        parts.append(str(profile_city))
    if not parts:
        parts = [str(profile_code), str(profile_name)]
    parts.append(f"tg:{message.from_user.id if message.from_user else 0}")
    topic_name = " | ".join(parts)[:120]
    try:
        created = await message.bot.create_forum_topic(chat_id=target_chat_id, name=topic_name)
    except Exception:
        return int(default_topic_id) if default_topic_id else None
    topic_id = int(created.message_thread_id)
    if message.from_user:
        await topic_dialog_store.set_user_topic(
            chat_id=target_chat_id,
            platform=Platform.TELEGRAM.value,
            platform_user_id=message.from_user.id,
            topic_id=topic_id,
        )
    return topic_id


async def handle_questions_process_callback(
    callback: CallbackQuery,
    *,
    action: str,
    questions_alert_store: QuestionsAlertStore,
    callback_codec: CallbackCodec,
) -> bool:
    if not callback.from_user or not callback.message:
        return False

    if action.startswith("questions:processed:"):
        await callback.answer("Уже обработано", show_alert=True)
        return True

    if not action.startswith("questions:process:"):
        return False

    alert_token = action.split(":", maxsplit=2)[2].strip()
    if not alert_token:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return True

    alert = await questions_alert_store.get(alert_token)
    if not alert:
        await callback.answer("Запись не найдена", show_alert=True)
        return True

    if int(alert.get("questions_chat_id") or 0) != int(callback.message.chat.id):
        await callback.answer("Некорректный чат", show_alert=True)
        return True

    stored_message_id = alert.get("questions_message_id")
    if stored_message_id and int(stored_message_id) != int(callback.message.message_id):
        await callback.answer("Сообщение устарело", show_alert=True)
        return True

    if alert.get("processed_at"):
        await callback.answer("Уже обработано", show_alert=True)
        return True

    processed_at = format_omsk_now()
    processed_by = processor_display_name(callback.from_user)
    await questions_alert_store.mark_processed(alert_token, processed_by, processed_at)

    dialog_link = build_tg_forum_message_link(
        chat_id=int(alert["dialog_chat_id"]),
        message_id=int(alert["dialog_message_id"]),
        topic_id=int(alert["dialog_topic_id"]),
    )

    current_text = callback.message.text or callback.message.caption or ""
    updated_text = current_text
    if "✅ Обработано:" not in current_text:
        updated_text = f"{current_text.rstrip()}\n✅ Обработано: {processed_by} — {processed_at}"

    processed_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🟢 {processed_at}",
                    callback_data=callback_codec.encode_public(f"questions:processed:{alert_token}"),
                )
            ]
        ]
    )

    try:
        if callback.message.text:
            await callback.message.edit_text(
                updated_text,
                parse_mode="HTML",
                reply_markup=processed_keyboard,
                disable_web_page_preview=True,
            )
        else:
            await callback.message.edit_caption(
                caption=updated_text,
                parse_mode="HTML",
                reply_markup=processed_keyboard,
            )
    except Exception:
        pass

    await callback.answer(url=dialog_link)
    return True
