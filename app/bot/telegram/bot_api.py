from __future__ import annotations

from typing import Any

from aiogram import Bot


async def api_copy_message(bot: Bot, /, **kwargs: Any):
    """Вызов Telegram copyMessage без side-effect зеркалирования DialogMirrorBot."""
    return await Bot.copy_message(bot, **kwargs)


async def api_send_message(bot: Bot, /, **kwargs: Any):
    """Вызов Telegram sendMessage без side-effect зеркалирования DialogMirrorBot."""
    return await Bot.send_message(bot, **kwargs)
