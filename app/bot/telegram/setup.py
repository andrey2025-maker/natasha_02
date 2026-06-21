from __future__ import annotations

from aiogram import Dispatcher

from app.bot.telegram.defaults import TELEGRAM_BOT_DEFAULTS
from app.bot.telegram.dialog_mirror_scheduler import DialogMirrorScheduler
from app.bot.telegram.handlers.admin import build_admin_router
from app.bot.telegram.handlers.buyout import build_buyout_router
from app.bot.telegram.handlers.profile import build_profile_router
from app.bot.telegram.handlers.questions import build_questions_router
from app.bot.telegram.handlers.start import build_start_router
from app.bot.telegram.middleware.dialog_mirror import (
    DialogMirrorCallbackAfterMiddleware,
    DialogMirrorIncomingMiddleware,
)
from app.bot.telegram.mirror_bot import DialogMirrorBot
from app.core.container import AppContainer


def build_telegram_bot_and_dispatcher(container: AppContainer) -> tuple[DialogMirrorBot, Dispatcher]:
    mirror_scheduler = DialogMirrorScheduler()
    bot = DialogMirrorBot(
        container=container,
        mirror_scheduler=mirror_scheduler,
        token=container.settings.telegram.bot_token,
        default=TELEGRAM_BOT_DEFAULTS,
    )
    dispatcher = Dispatcher()
    dispatcher.message.outer_middleware(DialogMirrorIncomingMiddleware(container, mirror_scheduler))
    dispatcher.callback_query.middleware(DialogMirrorCallbackAfterMiddleware())
    dispatcher.include_router(build_start_router(container))
    dispatcher.include_router(build_questions_router(container))
    dispatcher.include_router(build_buyout_router(container))
    dispatcher.include_router(build_admin_router(container))
    dispatcher.include_router(build_profile_router(container))
    return bot, dispatcher
