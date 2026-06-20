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
from app.services.dialog_topic_profile_sync import (
    build_topic_name_from_profile,
    refresh_dialog_topic_profile,
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


async def ensure_dialog_topic_for_telegram_user(
    *,
    bot,
    chat_id: int,
    tg_user_id: int,
    group_topics_store: GroupTopicsStore,
    topic_dialog_store: TopicDialogStore,
    profile=None,
    is_admin: bool = False,
    default_topic_id: int | None = None,
) -> int | None:
    _ = default_topic_id
    existing = await topic_dialog_store.get_user_topic(
        chat_id=int(chat_id),
        platform=Platform.TELEGRAM.value,
        platform_user_id=int(tg_user_id),
    )
    if existing:
        return int(existing)
    selected_parts = await group_topics_store.get_topic_name_parts()
    topic_name = build_topic_name_from_profile(
        profile,
        int(tg_user_id),
        selected_parts,
        is_admin=is_admin,
    )
    try:
        created = await bot.create_forum_topic(chat_id=int(chat_id), name=topic_name)
    except Exception:
        return None
    topic_id = int(created.message_thread_id)
    await topic_dialog_store.set_user_topic(
        chat_id=int(chat_id),
        platform=Platform.TELEGRAM.value,
        platform_user_id=int(tg_user_id),
        topic_id=topic_id,
    )
    return topic_id


async def forward_message_to_dialog_topic(
    message: Message,
    *,
    container: AppContainer,
    group_topics_store: GroupTopicsStore,
    notification_settings_store: NotificationSettingsStore,
    topic_dialog_store: TopicDialogStore,
    is_admin: bool | None = None,
) -> tuple[int, int, int] | None:
    if not message.from_user:
        return None

    profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, message.from_user.id)
    user_is_admin = await container.admin_service.is_admin(message.from_user.id)
    if is_admin is None:
        is_admin = user_is_admin
    admin_topic = user_is_admin and profile is None
    topics = await group_topics_store.ensure_all_system_topics(message.bot)
    if not topics:
        return None
    logs_chat_id = int(topics["chat_id"])
    logs_default_topic_id = topics.get("logs")

    dialog_topic_id = await ensure_dialog_topic_for_telegram_user(
        bot=message.bot,
        chat_id=int(logs_chat_id),
        tg_user_id=message.from_user.id,
        group_topics_store=group_topics_store,
        topic_dialog_store=topic_dialog_store,
        profile=profile,
        is_admin=admin_topic,
        default_topic_id=logs_default_topic_id,
    )
    if not dialog_topic_id:
        return None

    await refresh_dialog_topic_profile(
        message.bot,
        container=container,
        tg_user_id=message.from_user.id,
        group_topics_store=group_topics_store,
        topic_dialog_store=topic_dialog_store,
        notification_settings_store=notification_settings_store,
        is_admin=admin_topic,
    )

    notify_kind = "button" if is_admin else "user"
    disable_notification = await notification_settings_store.should_disable_notification(notify_kind)
    try:
        dialog_copy = await message.bot.copy_message(
            chat_id=int(logs_chat_id),
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=int(dialog_topic_id),
            disable_notification=disable_notification,
        )
    except Exception:
        return None

    await topic_dialog_store.bind_topic_message_to_user(
        chat_id=int(logs_chat_id),
        topic_id=int(dialog_topic_id),
        topic_message_id=int(dialog_copy.message_id),
        platform=Platform.TELEGRAM.value,
        platform_user_id=message.from_user.id,
    )
    return int(logs_chat_id), int(dialog_topic_id), int(dialog_copy.message_id)


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
    topics = await group_topics_store.ensure_all_system_topics(message.bot)
    if not topics:
        if send_ack:
            await message.answer(
                "Группа не настроена. Админу: Утилиты → Группа → укажите chat_id."
            )
        return
    logs_chat_id = int(topics["chat_id"])
    logs_default_topic_id = topics.get("logs")
    questions_chat_id = int(topics["chat_id"])
    questions_topic_id = topics.get("questions")
    if not questions_topic_id:
        if send_ack:
            await message.answer("Передал вопрос менеджеру. Ответим в этом чате как можно скорее.")
        return

    copied = await forward_message_to_dialog_topic(
        message,
        container=container,
        group_topics_store=group_topics_store,
        notification_settings_store=notification_settings_store,
        topic_dialog_store=topic_dialog_store,
        is_admin=False,
    )
    if not copied:
        return

    logs_chat_id, dialog_topic_id, dialog_message_id = copied
    disable_notification = await notification_settings_store.should_disable_notification("user")

    dialog_link = build_tg_forum_message_link(
        chat_id=int(logs_chat_id),
        message_id=int(dialog_message_id),
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
        dialog_message_id=int(dialog_message_id),
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
    is_admin: bool = False,
) -> int | None:
    if not message.from_user:
        return default_topic_id
    return await ensure_dialog_topic_for_telegram_user(
        bot=message.bot,
        chat_id=int(target_chat_id),
        tg_user_id=message.from_user.id,
        group_topics_store=group_topics_store,
        topic_dialog_store=topic_dialog_store,
        profile=profile,
        is_admin=is_admin,
        default_topic_id=default_topic_id,
    )


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
