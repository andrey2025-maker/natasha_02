from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DialogMirrorScheduler:
    """Serializes mirror jobs per private chat and limits global concurrency."""

    def __init__(self, *, max_concurrent_chats: int = 64, idle_timeout_seconds: float = 120.0) -> None:
        self._global_sem = asyncio.Semaphore(max_concurrent_chats)
        self._idle_timeout_seconds = idle_timeout_seconds
        self._workers: dict[int, _ChatMirrorWorker] = {}
        self._workers_lock = asyncio.Lock()
        from app.bot.telegram.topic_profile_refresh_debouncer import TopicProfileRefreshDebouncer

        self.topic_profile_debouncer = TopicProfileRefreshDebouncer(self)

    def submit(
        self,
        chat_id: int,
        factory: Callable[[], Awaitable[T]],
        *,
        label: str = "mirror",
    ) -> asyncio.Task[T | None]:
        """Queue mirror work for chat_id; jobs for one chat run sequentially."""
        loop = asyncio.get_running_loop()
        if chat_id <= 0:
            task: asyncio.Task[T | None] = loop.create_task(self._noop_result())
            return task
        task = loop.create_task(self._submit(chat_id, factory, label=label), name=f"mirror-{label}-{chat_id}")
        task.add_done_callback(_log_task_failure)
        return task

    def submit_fire_and_forget(
        self,
        chat_id: int,
        factory: Callable[[], Awaitable[Any]],
        *,
        label: str = "mirror",
    ) -> None:
        self.submit(chat_id, factory, label=label)

    @staticmethod
    async def _noop_result() -> None:
        return None

    async def _submit(
        self,
        chat_id: int,
        factory: Callable[[], Awaitable[T]],
        *,
        label: str,
    ) -> T | None:
        worker = await self._get_worker(chat_id)
        return await worker.submit(factory, label=label)

    async def _get_worker(self, chat_id: int) -> _ChatMirrorWorker:
        async with self._workers_lock:
            worker = self._workers.get(chat_id)
            if worker is None:
                worker = _ChatMirrorWorker(
                    chat_id,
                    self._global_sem,
                    self._idle_timeout_seconds,
                    self._drop_worker,
                )
                self._workers[chat_id] = worker
            return worker

    async def _drop_worker(self, chat_id: int, worker: _ChatMirrorWorker) -> None:
        async with self._workers_lock:
            current = self._workers.get(chat_id)
            if current is worker and worker.is_idle():
                self._workers.pop(chat_id, None)


class _ChatMirrorWorker:
    def __init__(
        self,
        chat_id: int,
        global_sem: asyncio.Semaphore,
        idle_timeout_seconds: float,
        on_idle: Callable[[int, _ChatMirrorWorker], Awaitable[Any]],
    ) -> None:
        self.chat_id = chat_id
        self._global_sem = global_sem
        self._idle_timeout_seconds = idle_timeout_seconds
        self._on_idle = on_idle
        self._queue: asyncio.Queue[tuple[Callable[[], Awaitable[Any]], asyncio.Future[Any], str] | None] = (
            asyncio.Queue()
        )
        self._worker_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()

    def is_idle(self) -> bool:
        return self._queue.empty() and (self._worker_task is None or self._worker_task.done())

    async def submit(
        self,
        factory: Callable[[], Awaitable[T]],
        *,
        label: str,
    ) -> T | None:
        loop = asyncio.get_running_loop()
        result_future: asyncio.Future[T | None] = loop.create_future()

        async def runner() -> T | None:
            try:
                async with self._global_sem:
                    return await factory()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Dialog mirror job failed (%s, chat_id=%s)", label, self.chat_id)
                return None

        await self._queue.put((runner, result_future, label))
        await self._ensure_worker()
        return await result_future

    async def _ensure_worker(self) -> None:
        async with self._start_lock:
            if self._worker_task is None or self._worker_task.done():
                self._worker_task = asyncio.create_task(
                    self._worker_loop(),
                    name=f"dialog-mirror-{self.chat_id}",
                )

    async def _worker_loop(self) -> None:
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=self._idle_timeout_seconds)
            except asyncio.TimeoutError:
                break
            if item is None:
                self._queue.task_done()
                break
            runner, result_future, label = item
            try:
                result = await runner()
                if not result_future.done():
                    result_future.set_result(result)
            except asyncio.CancelledError:
                if not result_future.done():
                    result_future.cancel()
                raise
            except Exception:
                logger.exception("Dialog mirror worker job failed (%s, chat_id=%s)", label, self.chat_id)
                if not result_future.done():
                    result_future.set_result(None)
            finally:
                self._queue.task_done()

        await self._on_idle(self.chat_id, self)


def _log_task_failure(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error("Dialog mirror submit task failed: %s", exc, exc_info=exc)
