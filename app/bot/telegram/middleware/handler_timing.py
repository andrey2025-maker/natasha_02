from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)

_SLOW_HANDLER_MS = 500.0


def _event_label(event: TelegramObject) -> str:
    if isinstance(event, Message):
        text = (event.text or event.caption or "")[:40]
        return f"message chat={event.chat.id} text={text!r}"
    if isinstance(event, CallbackQuery):
        data = (event.data or "")[:48]
        return f"callback chat={event.message.chat.id if event.message else '?'} data={data!r}"
    return type(event).__name__


class HandlerTimingMiddleware(BaseMiddleware):
    """Логирует медленные обработчики — совпадает с Duration в aiogram.event."""

    def __init__(self, *, threshold_ms: float = _SLOW_HANDLER_MS) -> None:
        self._threshold_ms = threshold_ms

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        started = time.perf_counter()
        try:
            return await handler(event, data)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            if elapsed_ms < self._threshold_ms:
                return
            handler_obj = data.get("handler")
            handler_callback = getattr(handler_obj, "callback", handler)
            handler_name = getattr(handler_callback, "__name__", repr(handler_callback))
            logger.warning(
                "Slow handler %.0f ms: %s | %s",
                elapsed_ms,
                handler_name,
                _event_label(event),
            )
