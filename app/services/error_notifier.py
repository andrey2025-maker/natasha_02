from __future__ import annotations

import traceback
from dataclasses import dataclass

import aiohttp

from app.core.config import AppSettings
from app.services.admin_tools_service import GroupTopicsStore


@dataclass(slots=True)
class ErrorNotifier:
    settings: AppSettings

    async def notify_exception(self, source: str, exc: BaseException) -> None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        text = (
            f"Ошибка процесса: {source}\n"
            f"Тип: {type(exc).__name__}\n"
            f"Сообщение: {exc}\n\n"
            f"Traceback:\n{tb}"
        )
        await self.notify_text(source=source, text=text)

    async def notify_text(self, source: str, text: str) -> None:
        await self._send_tg(source, text)
        await self._send_vk(source, text)

    async def _send_tg(self, source: str, text: str) -> None:
        store = GroupTopicsStore(self.settings.database.dsn)
        chat_id, topic_id = await store.get_tg_topic("logs")
        if not chat_id:
            chat_id = self.settings.telegram.error_chat_id or self.settings.telegram.main_admin_id
            topic_id = self.settings.telegram.error_topic_id
        payload = {
            "chat_id": chat_id,
            "text": self._clip(f"[{source}] {text}", 3900),
        }
        if topic_id:
            payload["message_thread_id"] = topic_id
        url = f"https://api.telegram.org/bot{self.settings.telegram.bot_token}/sendMessage"
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(url, data=payload)
        except Exception:
            return

    async def _send_vk(self, source: str, text: str) -> None:
        if self.settings.vk is None:
            return
        store = GroupTopicsStore(self.settings.database.dsn)
        peer_id = await store.get_vk_logs_peer_id()
        if not peer_id:
            peer_id = self.settings.vk.error_peer_id
        if not peer_id:
            return
        url = "https://api.vk.com/method/messages.send"
        payload = {
            "access_token": self.settings.vk.bot_token,
            "v": "5.199",
            "peer_id": peer_id,
            "random_id": 0,
            "message": self._clip(f"[{source}] {text}", 3900),
        }
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(url, data=payload)
        except Exception:
            return

    @staticmethod
    def _clip(value: str, limit: int) -> str:
        text = value.strip()
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."
