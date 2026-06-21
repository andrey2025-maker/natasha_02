from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from datetime import datetime
from typing import Any

from app.domain.enums import DeliveryFlowType, DialogState, OrderStatus
from app.domain.models import BuyoutOrder, UserProfile, UserSession
from app.bot.telegram.fsm_utils import fsm_prompt
from app.storage.interfaces import BuyoutOrderRepository, SessionRepository

_TRACK_MAX_LEN = 128
_TRACK_TOKEN_SPLIT = re.compile(r"[\s,;\n]+")


@dataclass(slots=True)
class TrackFlowResponse:
    text: str
    state: DialogState
    state_data: dict[str, Any] = field(default_factory=dict)
    reply_markup: Any = None


class TrackFlowService:
    def __init__(self, session_repo: SessionRepository, order_repo: BuyoutOrderRepository) -> None:
        self._sessions = session_repo
        self._orders = order_repo

    async def show_mode_menu(self, session: UserSession) -> TrackFlowResponse:
        session.state = DialogState.IDLE
        session.state_data = _clear_track_state(session.state_data)
        await self._sessions.save(session)
        return TrackFlowResponse(
            text=(
                "<b>📦 Трек номер</b>\n"
                "Выберите, как будете вводить данные:"
            ),
            state=DialogState.IDLE,
            state_data=dict(session.state_data),
        )

    async def start_mode(self, session: UserSession, mode: str) -> TrackFlowResponse:
        normalized = mode.strip().lower()
        if normalized not in {"numbers", "comments"}:
            return TrackFlowResponse(
                text="Неизвестный режим ввода.",
                state=DialogState.IDLE,
                state_data=_clear_track_state(session.state_data),
            )
        state_data = _clear_track_state(session.state_data)
        state_data["track_input_mode"] = normalized
        state_data["track_pending"] = []
        session.state = DialogState.TRACK_WAIT_INPUT
        session.state_data = state_data
        await self._sessions.save(session)
        return TrackFlowResponse(
            text=fsm_prompt(_input_prompt()),
            state=session.state,
            state_data=dict(session.state_data),
        )

    async def continue_input(self, session: UserSession) -> TrackFlowResponse:
        if not session.state_data.get("track_input_mode"):
            return await self.show_mode_menu(session)
        session.state = DialogState.TRACK_WAIT_INPUT
        await self._sessions.save(session)
        return TrackFlowResponse(
            text=fsm_prompt(_input_prompt()),
            state=session.state,
            state_data=dict(session.state_data),
        )

    async def handle_text(self, session: UserSession, text: str) -> TrackFlowResponse:
        mode = str(session.state_data.get("track_input_mode") or "").strip().lower()
        if mode not in {"numbers", "comments"}:
            return TrackFlowResponse(
                text="Сначала выберите режим ввода в разделе «Трек номер».",
                state=DialogState.IDLE,
                state_data=_clear_track_state(session.state_data),
            )

        parsed, error = _parse_track_input(text, mode)
        if error:
            return TrackFlowResponse(
                text=error,
                state=session.state,
                state_data=dict(session.state_data),
            )
        if not parsed:
            return TrackFlowResponse(
                text="Не распознаны трек-номера. Проверьте формат и попробуйте снова.",
                state=session.state,
                state_data=dict(session.state_data),
            )

        pending = list(session.state_data.get("track_pending") or [])
        pending.extend(parsed)
        session.state_data = _merge_track_state(session.state_data, pending, mode)
        session.state = DialogState.TRACK_WAIT_CONTINUE
        await self._sessions.save(session)
        return TrackFlowResponse(
            text=_batch_summary(parsed, len(pending)),
            state=session.state,
            state_data=dict(session.state_data),
        )

    async def take_pending_and_reset(self, session: UserSession) -> tuple[list[dict[str, str]], TrackFlowResponse]:
        pending = list(session.state_data.get("track_pending") or [])
        session.state = DialogState.IDLE
        session.state_data = _clear_track_state(session.state_data)
        await self._sessions.save(session)
        if not pending:
            return [], TrackFlowResponse(
                text="Нет трек-номеров для отправки.",
                state=DialogState.IDLE,
                state_data={},
            )
        return pending, TrackFlowResponse(
            text=f"Отправлено трек-номеров: {len(pending)}.",
            state=DialogState.IDLE,
            state_data={},
        )

    async def create_orders_from_tracks(
        self,
        profile: UserProfile,
        entries: list[dict[str, str]],
    ) -> list[BuyoutOrder]:
        created: list[BuyoutOrder] = []
        now = datetime.utcnow()
        for entry in entries:
            track = str(entry.get("track", "")).strip()
            if not track:
                continue
            comment = str(entry.get("comment", "")).strip()
            count = await self._orders.count_for_user(profile.id)
            order_number = f"{profile.code}/{count + 1}T"
            order = BuyoutOrder(
                id=0,
                user_profile_id=profile.id,
                order_number=order_number,
                flow_type=DeliveryFlowType.SELF_BUYOUT,
                status=OrderStatus.IN_TRANSIT,
                product_url="—",
                quantity_text=comment or "—",
                track_number=track,
                manager_comment="",
                created_at=now,
                updated_at=now,
            )
            created.append(await self._orders.create(order))
        return created

    async def clear(self, session: UserSession) -> None:
        session.state = DialogState.IDLE
        session.state_data = _clear_track_state(session.state_data)
        await self._sessions.save(session)


def _input_prompt() -> str:
    return (
        "<b>🔢 Ввод трек-номеров</b>\n"
        "Отправьте номера через пробел, запятую или каждый с новой строки."
    )


def _batch_summary(last_batch: list[dict[str, str]], total: int) -> str:
    lines = [
        f"Принято в этом сообщении: <b>{len(last_batch)}</b>.",
        f"Всего на отправку: <b>{total}</b>.",
        "",
        "Добавить ещё или отправить все?",
    ]
    preview = last_batch[:5]
    if preview:
        lines.append("")
        for item in preview:
            track = escape(str(item.get("track", "")))
            comment = str(item.get("comment", "")).strip()
            if comment:
                lines.append(f"• <code>{track}</code> — {escape(comment)}")
            else:
                lines.append(f"• <code>{track}</code>")
        if len(last_batch) > len(preview):
            lines.append("…")
    return "\n".join(lines)


def _parse_track_input(text: str, mode: str) -> tuple[list[dict[str, str]], str | None]:
    raw = (text or "").strip()
    if not raw:
        return [], "Сообщение пустое. Введите трек-номера."

    if mode == "numbers":
        entries = _parse_numbers_only(raw)
    else:
        entries = _parse_with_comments(raw)

    result: list[dict[str, str]] = []
    for track, comment in entries:
        normalized_track = track.strip()
        if not normalized_track:
            continue
        if len(normalized_track) > _TRACK_MAX_LEN:
            return [], f"Трек «{escape(normalized_track[:32])}…» слишком длинный (максимум {_TRACK_MAX_LEN} символов)."
        result.append({"track": normalized_track, "comment": comment.strip()})
    return result, None


def _parse_numbers_only(text: str) -> list[tuple[str, str]]:
    tokens = [token.strip() for token in _TRACK_TOKEN_SPLIT.split(text) if token.strip()]
    return [(token, "") for token in tokens]


def _parse_with_comments(text: str) -> list[tuple[str, str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        parts = line.split(None, 1)
        if len(parts) == 2:
            entries.append((parts[0], parts[1]))
            index += 1
            continue
        if index + 1 < len(lines):
            entries.append((parts[0], lines[index + 1]))
            index += 2
            continue
        entries.append((parts[0], ""))
        index += 1
    return entries


def _merge_track_state(existing: dict[str, Any], pending: list[dict[str, str]], mode: str) -> dict[str, Any]:
    merged = _clear_track_state(existing)
    merged["track_input_mode"] = mode
    merged["track_pending"] = pending
    return merged


def _clear_track_state(existing: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    merged.pop("track_input_mode", None)
    merged.pop("track_pending", None)
    return merged
