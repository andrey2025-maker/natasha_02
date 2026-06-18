from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Deque

from app.core.config import RateLimitSettings


@dataclass(slots=True)
class UserRateState:
    second_events: Deque[datetime] = field(default_factory=deque)
    minute_events: Deque[datetime] = field(default_factory=deque)
    last_messages: Deque[str] = field(default_factory=deque)


class RateLimitService:
    def __init__(self, settings: RateLimitSettings) -> None:
        self._settings = settings
        self._states: dict[str, UserRateState] = defaultdict(UserRateState)

    def allow_request(self, user_key: str, message_text: str | None = None) -> bool:
        now = datetime.utcnow()
        state = self._states[user_key]

        self._trim(state.second_events, now - timedelta(seconds=1))
        self._trim(state.minute_events, now - timedelta(minutes=1))

        if len(state.second_events) >= self._settings.per_second_limit:
            return False
        if len(state.minute_events) >= self._settings.per_minute_limit:
            return False

        if message_text:
            if state.last_messages.maxlen != self._settings.duplicate_message_limit:
                state.last_messages = deque(state.last_messages, maxlen=self._settings.duplicate_message_limit)
            if len(state.last_messages) == state.last_messages.maxlen and all(
                msg == message_text for msg in state.last_messages
            ):
                return False
            state.last_messages.append(message_text)

        state.second_events.append(now)
        state.minute_events.append(now)
        return True

    def validate_user_payload_size(self, text_size: int, media_size_mb: int | None = None) -> bool:
        if text_size > self._settings.user_max_text_len:
            return False
        if media_size_mb is not None and media_size_mb > self._settings.user_max_media_size_mb:
            return False
        return True

    @staticmethod
    def _trim(events: Deque[datetime], threshold: datetime) -> None:
        while events and events[0] < threshold:
            events.popleft()
