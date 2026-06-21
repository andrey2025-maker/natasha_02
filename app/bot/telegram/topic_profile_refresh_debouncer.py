from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.container import AppContainer
from app.services.admin_tools_service import (
    AdminProfileCommentStore,
    GroupTopicsStore,
    NotificationSettingsStore,
    TopicDialogStore,
)

if TYPE_CHECKING:
    from app.bot.telegram.dialog_mirror_scheduler import DialogMirrorScheduler

logger = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_SECONDS = 2.0


@dataclass(slots=True)
class TopicProfileRefreshRequest:
    bot: Any
    container: AppContainer
    tg_user_id: int
    group_topics_store: GroupTopicsStore | None = None
    topic_dialog_store: TopicDialogStore | None = None
    profile_comment_store: AdminProfileCommentStore | None = None
    notification_settings_store: NotificationSettingsStore | None = None
    is_admin: bool | None = None


@dataclass(slots=True)
class _DebounceEntry:
    request: TopicProfileRefreshRequest
    generation: int = 0
    timer: asyncio.Task[None] | None = None


class TopicProfileRefreshDebouncer:
    """Coalesces frequent refresh requests per user into a single scheduler job."""

    def __init__(
        self,
        scheduler: DialogMirrorScheduler,
        *,
        delay_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    ) -> None:
        self._scheduler = scheduler
        self._delay_seconds = delay_seconds
        self._entries: dict[int, _DebounceEntry] = {}
        self._lock = asyncio.Lock()

    def schedule(self, request: TopicProfileRefreshRequest, *, immediate: bool = False) -> None:
        if request.tg_user_id <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "Cannot schedule topic profile refresh without running event loop (chat_id=%s)",
                request.tg_user_id,
            )
            return
        loop.create_task(
            self._schedule_safe(request, immediate=immediate),
            name=f"topic-refresh-schedule-{request.tg_user_id}",
        )

    async def _schedule_safe(self, request: TopicProfileRefreshRequest, *, immediate: bool) -> None:
        try:
            await self._schedule_locked(request, immediate=immediate)
        except Exception:
            logger.exception(
                "Failed to schedule topic profile refresh (chat_id=%s)",
                request.tg_user_id,
            )

    async def _schedule_locked(self, request: TopicProfileRefreshRequest, *, immediate: bool) -> None:
        tg_user_id = int(request.tg_user_id)
        flush_request: TopicProfileRefreshRequest | None = None

        async with self._lock:
            entry = self._entries.get(tg_user_id)
            if entry is None:
                entry = _DebounceEntry(request=request)
                self._entries[tg_user_id] = entry
            else:
                entry.request = request

            if entry.timer is not None:
                entry.timer.cancel()
                entry.timer = None

            if immediate:
                flush_request = entry.request
                self._entries.pop(tg_user_id, None)
            else:
                entry.generation += 1
                generation = entry.generation
                entry.timer = asyncio.create_task(
                    self._fire_after_delay(tg_user_id, generation),
                    name=f"topic-refresh-debounce-{tg_user_id}",
                )

        if flush_request is not None:
            await self._flush(flush_request)

    async def _fire_after_delay(self, tg_user_id: int, generation: int) -> None:
        try:
            await asyncio.sleep(self._delay_seconds)
        except asyncio.CancelledError:
            return

        async with self._lock:
            entry = self._entries.get(tg_user_id)
            if entry is None or entry.generation != generation:
                return
            request = entry.request
            entry.timer = None
            self._entries.pop(tg_user_id, None)

        await self._flush(request)

    async def _flush(self, request: TopicProfileRefreshRequest) -> None:
        from app.services.dialog_topic_profile_sync import refresh_dialog_topic_profile

        async def job() -> None:
            await refresh_dialog_topic_profile(
                request.bot,
                container=request.container,
                tg_user_id=int(request.tg_user_id),
                group_topics_store=request.group_topics_store,
                topic_dialog_store=request.topic_dialog_store,
                profile_comment_store=request.profile_comment_store,
                notification_settings_store=request.notification_settings_store,
                is_admin=request.is_admin,
            )

        self._scheduler.submit_fire_and_forget(int(request.tg_user_id), job, label="topic_profile_refresh")
