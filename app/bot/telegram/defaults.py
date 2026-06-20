from __future__ import annotations

from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

TELEGRAM_BOT_DEFAULTS = DefaultBotProperties(
    parse_mode=ParseMode.HTML,
    link_preview_is_disabled=True,
)
