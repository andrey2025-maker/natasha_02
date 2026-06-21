from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from html import escape

from app.core.container import AppContainer
from app.domain.enums import Platform
from app.domain.models import UserProfile
from app.services.admin_tools_service import (
    AdminProfileCommentStore,
    GroupTopicsStore,
    NotificationSettingsStore,
    TopicDialogStore,
)
from app.bot.telegram.topic_profile_refresh_debouncer import (
    DEFAULT_DEBOUNCE_SECONDS,
    TopicProfileRefreshRequest,
)

logger = logging.getLogger(__name__)


def _h(value: object) -> str:
    return escape(str(value), quote=False)


def profile_state_emoji(profile: UserProfile | None, *, is_admin: bool = False) -> str:
    if profile is None:
        return "👑" if is_admin else "🟢"
    if profile.is_blocked_by_admin:
        return "⛔"
    if profile.blocked_bot:
        return "🔴"
    return "🟢"


def build_topic_profile_card_text(
    profile: UserProfile | None,
    *,
    tg_user_id: int,
    admin_comment: str | None = None,
    is_admin: bool = False,
) -> str:
    status = profile_state_emoji(profile, is_admin=is_admin)
    code = profile.code if profile and profile.code else ("ADMIN" if is_admin else "—")
    name_value = _h((profile.name if profile else "") or ("Админ" if is_admin else "Нет"))
    if profile and profile.telegram_user_id:
        name_value = (
            f"<a href='tg://user?id={int(profile.telegram_user_id)}'>{name_value}</a>"
        )
    elif is_admin:
        name_value = f"<a href='tg://user?id={int(tg_user_id)}'>{name_value}</a>"

    vk_value = "Нет"
    if profile and profile.vk_user_id:
        vk_id = int(profile.vk_user_id)
        vk_value = f"<a href='https://vk.com/id{vk_id}'>vk.com/id{vk_id}</a>"

    phone = _h((profile.phone if profile else "") or "Нет")
    city = _h((profile.city if profile else "") or "Нет")
    has_passport = "Да" if profile and profile.has_passport else "Нет"
    comment_line = _h(admin_comment.strip()) if admin_comment and admin_comment.strip() else "—"
    tg_id = profile.telegram_user_id if profile and profile.telegram_user_id else tg_user_id
    last_activity = (
        profile.last_activity_at.strftime("%Y-%m-%d %H:%M")
        if profile and profile.last_activity_at
        else "—"
    )
    created_at = (
        profile.created_at.strftime("%Y-%m-%d %H:%M")
        if profile and profile.created_at
        else "—"
    )

    title = "👑 <b>Профиль админа</b>" if is_admin and profile is None else "👤 <b>Профиль</b>"
    return (
        f"{title}\n"
        f"{status} 🆔 Код: {_h(code)}\n"
        f"👤 Имя: {name_value}\n"
        f"📞 Тел: {phone}\n"
        f"🏙 Город: {city}\n"
        f"🌍 Загран Паспорт: {has_passport}\n"
        f"💬 Комментарий админа: {comment_line}\n"
        f"🆔 ID: {_h(tg_id)}\n"
        f"🔗 ВК: {vk_value}\n"
        f"🕒 Последняя активность: {_h(last_activity)}\n"
        f"📅 Дата регистрации: {_h(created_at)}"
    )


def build_topic_name_from_profile(
    profile: UserProfile | None,
    tg_user_id: int,
    selected_parts: list[str],
    *,
    is_admin: bool = False,
) -> str:
    profile_code = profile.code if profile and profile.code else ("ADMIN" if is_admin else "—")
    profile_name = (profile.name if profile else "") or ("Админ" if is_admin else "без имени")
    profile_phone = (profile.phone if profile else "") or "без телефона"
    profile_city = (profile.city if profile else "") or "без города"
    parts: list[str] = []
    if "code" in selected_parts:
        parts.append(str(profile_code))
    if "name" in selected_parts:
        parts.append(str(profile_name))
    if "phone" in selected_parts:
        parts.append(str(profile_phone))
    if "city" in selected_parts:
        parts.append(str(profile_city))
    if not parts:
        parts = [str(profile_code), str(profile_name)]
    parts.append(f"tg:{int(tg_user_id)}")
    return " | ".join(parts)[:120]


async def refresh_dialog_topic_profile(
    bot,
    *,
    container: AppContainer,
    tg_user_id: int,
    group_topics_store: GroupTopicsStore | None = None,
    topic_dialog_store: TopicDialogStore | None = None,
    profile_comment_store: AdminProfileCommentStore | None = None,
    notification_settings_store: NotificationSettingsStore | None = None,
    is_admin: bool | None = None,
) -> None:
    if tg_user_id <= 0:
        return

    dsn = container.settings.database.dsn
    group_topics_store = group_topics_store or GroupTopicsStore(dsn)
    topic_dialog_store = topic_dialog_store or TopicDialogStore(dsn)
    profile_comment_store = profile_comment_store or AdminProfileCommentStore(dsn)
    notification_settings_store = notification_settings_store or NotificationSettingsStore(dsn)

    logs_chat_id, _ = await group_topics_store.get_tg_topic("logs")
    if not logs_chat_id:
        return

    topic_id = await topic_dialog_store.get_user_topic(
        chat_id=int(logs_chat_id),
        platform=Platform.TELEGRAM.value,
        platform_user_id=int(tg_user_id),
    )
    if not topic_id:
        return

    profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, int(tg_user_id))
    if is_admin is None:
        is_admin = await container.admin_service.is_admin(int(tg_user_id)) and profile is None

    admin_comment = None
    if profile:
        admin_comment = await profile_comment_store.get_comment(profile.code)

    card_text = build_topic_profile_card_text(
        profile,
        tg_user_id=int(tg_user_id),
        admin_comment=admin_comment,
        is_admin=bool(is_admin),
    )

    selected_parts = await group_topics_store.get_topic_name_parts()
    topic_name = build_topic_name_from_profile(
        profile,
        int(tg_user_id),
        selected_parts,
        is_admin=bool(is_admin),
    )

    disable_notification = await notification_settings_store.should_disable_notification("bot")
    pinned_id = await topic_dialog_store.get_pinned_profile_message_id(
        chat_id=int(logs_chat_id),
        platform=Platform.TELEGRAM.value,
        platform_user_id=int(tg_user_id),
    )

    try:
        if pinned_id:
            await bot.edit_message_text(
                chat_id=int(logs_chat_id),
                message_id=int(pinned_id),
                text=card_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        else:
            sent = await bot.send_message(
                chat_id=int(logs_chat_id),
                text=card_text,
                parse_mode="HTML",
                message_thread_id=int(topic_id),
                disable_notification=disable_notification,
                disable_web_page_preview=True,
            )
            pinned_id = int(sent.message_id)
            await topic_dialog_store.set_pinned_profile_message_id(
                chat_id=int(logs_chat_id),
                platform=Platform.TELEGRAM.value,
                platform_user_id=int(tg_user_id),
                message_id=pinned_id,
            )
            try:
                await bot.pin_chat_message(
                    chat_id=int(logs_chat_id),
                    message_id=pinned_id,
                    disable_notification=True,
                )
            except Exception:
                pass
    except Exception:
        if pinned_id:
            return
        try:
            sent = await bot.send_message(
                chat_id=int(logs_chat_id),
                text=card_text,
                parse_mode="HTML",
                message_thread_id=int(topic_id),
                disable_notification=disable_notification,
                disable_web_page_preview=True,
            )
            pinned_id = int(sent.message_id)
            await topic_dialog_store.set_pinned_profile_message_id(
                chat_id=int(logs_chat_id),
                platform=Platform.TELEGRAM.value,
                platform_user_id=int(tg_user_id),
                message_id=pinned_id,
            )
            try:
                await bot.pin_chat_message(
                    chat_id=int(logs_chat_id),
                    message_id=pinned_id,
                    disable_notification=True,
                )
            except Exception:
                pass
        except Exception:
            return

    try:
        await bot.edit_forum_topic(
            chat_id=int(logs_chat_id),
            message_thread_id=int(topic_id),
            name=topic_name,
        )
    except Exception:
        pass


def schedule_refresh_dialog_topic_profile(
    bot,
    *,
    container: AppContainer,
    tg_user_id: int,
    group_topics_store: GroupTopicsStore | None = None,
    topic_dialog_store: TopicDialogStore | None = None,
    profile_comment_store: AdminProfileCommentStore | None = None,
    notification_settings_store: NotificationSettingsStore | None = None,
    is_admin: bool | None = None,
    immediate: bool = False,
) -> None:
    """Обновляет карточку профиля в теме диалога в фоне, не блокируя ответ пользователю."""
    if tg_user_id <= 0:
        return

    request = TopicProfileRefreshRequest(
        bot=bot,
        container=container,
        tg_user_id=int(tg_user_id),
        group_topics_store=group_topics_store,
        topic_dialog_store=topic_dialog_store,
        profile_comment_store=profile_comment_store,
        notification_settings_store=notification_settings_store,
        is_admin=is_admin,
    )

    debouncer = _resolve_topic_profile_debouncer(bot)
    if debouncer is not None:
        debouncer.schedule(request, immediate=immediate)
        return

    async def job() -> None:
        await refresh_dialog_topic_profile(
            bot,
            container=container,
            tg_user_id=int(tg_user_id),
            group_topics_store=group_topics_store,
            topic_dialog_store=topic_dialog_store,
            profile_comment_store=profile_comment_store,
            notification_settings_store=notification_settings_store,
            is_admin=is_admin,
        )

    if immediate:
        task = asyncio.create_task(
            _run_refresh_job(job, int(tg_user_id)),
            name=f"topic-profile-refresh-{int(tg_user_id)}",
        )
        task.add_done_callback(_log_refresh_task_failure)
        return

    asyncio.create_task(
        _run_debounced_fallback(job, int(tg_user_id), delay_seconds=DEFAULT_DEBOUNCE_SECONDS),
        name=f"topic-profile-refresh-debounce-{int(tg_user_id)}",
    )


async def _run_refresh_job(job: Callable[[], Awaitable[None]], tg_user_id: int) -> None:
    try:
        await job()
    except Exception:
        logger.exception("Background topic profile refresh failed (chat_id=%s)", tg_user_id)


async def _run_debounced_fallback(
    job: Callable[[], Awaitable[None]],
    tg_user_id: int,
    *,
    delay_seconds: float,
) -> None:
    try:
        await asyncio.sleep(delay_seconds)
        await _run_refresh_job(job, tg_user_id)
    except asyncio.CancelledError:
        return


def _resolve_mirror_scheduler(bot):
    from app.bot.telegram.mirror_bot import DialogMirrorBot

    if isinstance(bot, DialogMirrorBot):
        return bot.mirror_scheduler
    return None


def _resolve_topic_profile_debouncer(bot):
    scheduler = _resolve_mirror_scheduler(bot)
    if scheduler is not None:
        return scheduler.topic_profile_debouncer
    return None


def _log_refresh_task_failure(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error("Topic profile refresh task failed: %s", exc, exc_info=exc)
