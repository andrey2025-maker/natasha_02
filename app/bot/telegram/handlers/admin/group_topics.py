from __future__ import annotations

from aiogram.types import Message

from app.bot.telegram.handlers.questions_topic import ensure_dialog_topic_for_telegram_user
from app.core.container import AppContainer
from app.domain.enums import Platform
from app.domain.models import UserProfile
from app.services.admin_tools_service import GroupTopicsStore, TopicDialogStore
from app.services.dialog_topic_profile_sync import schedule_refresh_dialog_topic_profile


def _refresh_group_topic_profile(
    bot,
    *,
    container: AppContainer,
    profile: UserProfile | None = None,
    tg_user_id: int | None = None,
    group_topics_store: GroupTopicsStore | None = None,
    topic_dialog_store: TopicDialogStore | None = None,
    is_admin: bool | None = None,
    immediate: bool = True,
) -> None:
    target_id = tg_user_id
    if target_id is None and profile is not None:
        target_id = profile.telegram_user_id
    if not target_id:
        return
    schedule_refresh_dialog_topic_profile(
        bot,
        container=container,
        tg_user_id=int(target_id),
        group_topics_store=group_topics_store,
        topic_dialog_store=topic_dialog_store,
        is_admin=is_admin,
        immediate=immediate,
    )


async def _create_required_group_topics(
    *,
    bot,
    chat_id: int,
    group_topics_store: GroupTopicsStore,
    backup_service: BackupService,
    payment_target_store: PaymentReviewTargetStore,
) -> tuple[int, int, int, int] | None:
    await group_topics_store.set_tg_chat_id(chat_id)
    stored_chat_id, logs_topic_id = await group_topics_store.get_tg_topic("logs")
    _, payment_topic_id = await group_topics_store.get_tg_topic("payment")
    _, questions_topic_id = await group_topics_store.get_tg_topic("questions")
    _, buyout_topic_id = await group_topics_store.get_tg_topic("buyout")
    same_group = int(stored_chat_id or 0) == int(chat_id)

    async def _ensure_topic(existing_id: int | None, title: str) -> int:
        if same_group and existing_id:
            return int(existing_id)
        created = await bot.create_forum_topic(chat_id=chat_id, name=title)
        return int(created.message_thread_id)

    try:
        logs_topic_id = await _ensure_topic(logs_topic_id if same_group else None, "логи")
        payment_topic_id = await _ensure_topic(payment_topic_id if same_group else None, "оплата")
        questions_topic_id = await _ensure_topic(questions_topic_id if same_group else None, "вопросы")
        buyout_topic_id = await _ensure_topic(buyout_topic_id if same_group else None, "Выкупы")
    except Exception:
        return None

    await group_topics_store.set_tg_topics(
        logs_topic_id=logs_topic_id,
        payment_topic_id=payment_topic_id,
        questions_topic_id=questions_topic_id,
        buyout_topic_id=buyout_topic_id,
    )
    await backup_service.set_backup_target(chat_id=chat_id, topic_id=logs_topic_id)
    await payment_target_store.set_target(chat_id=chat_id, topic_id=payment_topic_id)
    return logs_topic_id, payment_topic_id, questions_topic_id, buyout_topic_id


async def _provision_topics_for_existing_telegram_profiles(
    *,
    bot,
    chat_id: int,
    container: AppContainer,
    group_topics_store: GroupTopicsStore,
    topic_dialog_store: TopicDialogStore,
) -> tuple[int, int, int]:
    created_count = 0
    existed_count = 0
    failed_count = 0
    seen_tg_ids: set[int] = set()
    _, logs_default_topic_id = await group_topics_store.get_tg_topic("logs")
    page = 1
    page_size = 200
    while True:
        chunk = await container.admin_service.list_profiles(page=page, page_size=page_size)
        if not chunk:
            break
        for profile in chunk:
            tg_user_id = int(profile.telegram_user_id or 0)
            if tg_user_id <= 0:
                continue
            seen_tg_ids.add(tg_user_id)
            existing = await topic_dialog_store.get_user_topic(
                chat_id=chat_id,
                platform=Platform.TELEGRAM.value,
                platform_user_id=tg_user_id,
            )
            if existing:
                existed_count += 1
                continue
            topic_id = await ensure_dialog_topic_for_telegram_user(
                bot=bot,
                chat_id=chat_id,
                tg_user_id=tg_user_id,
                group_topics_store=group_topics_store,
                topic_dialog_store=topic_dialog_store,
                profile=profile,
                is_admin=False,
                default_topic_id=logs_default_topic_id,
            )
            if topic_id:
                created_count += 1
                schedule_refresh_dialog_topic_profile(
                    bot,
                    container=container,
                    tg_user_id=tg_user_id,
                    group_topics_store=group_topics_store,
                    topic_dialog_store=topic_dialog_store,
                    is_admin=False,
                    immediate=False,
                )
            else:
                failed_count += 1
        if len(chunk) < page_size:
            break
        page += 1

    for admin_id in await container.admin_service.list_admins():
        tg_user_id = int(admin_id)
        if tg_user_id <= 0 or tg_user_id in seen_tg_ids:
            continue
        existing = await topic_dialog_store.get_user_topic(
            chat_id=chat_id,
            platform=Platform.TELEGRAM.value,
            platform_user_id=tg_user_id,
        )
        if existing:
            existed_count += 1
            continue
        profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, tg_user_id)
        admin_topic = profile is None
        topic_id = await ensure_dialog_topic_for_telegram_user(
            bot=bot,
            chat_id=chat_id,
            tg_user_id=tg_user_id,
            group_topics_store=group_topics_store,
            topic_dialog_store=topic_dialog_store,
            profile=profile,
            is_admin=admin_topic,
            default_topic_id=logs_default_topic_id,
        )
        if topic_id:
            created_count += 1
            schedule_refresh_dialog_topic_profile(
                bot,
                container=container,
                tg_user_id=tg_user_id,
                group_topics_store=group_topics_store,
                topic_dialog_store=topic_dialog_store,
                is_admin=admin_topic,
                immediate=False,
            )
        else:
            failed_count += 1

    return created_count, existed_count, failed_count


async def _archive_media_in_group_topic(
    message: Message,
    group_topics_store: GroupTopicsStore,
    label: str,
) -> tuple[int | None, int | None, int | None]:
    target_chat_id, target_topic_id = await group_topics_store.get_tg_topic("logs")
    if not target_chat_id:
        return None, None, None
    try:
        copied = await message.bot.copy_message(
            chat_id=target_chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=target_topic_id,
        )
    except Exception:
        return None, None, None
    if message.caption:
        try:
            await message.bot.send_message(
                chat_id=target_chat_id,
                text=f"media-archive:{label}",
                message_thread_id=target_topic_id,
                reply_to_message_id=copied.message_id,
            )
        except Exception:
            pass
    return int(target_chat_id), int(target_topic_id) if target_topic_id else None, int(copied.message_id)

