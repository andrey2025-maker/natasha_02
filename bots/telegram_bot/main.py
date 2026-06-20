from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from aiogram import Bot, Dispatcher

from app.bot.telegram.defaults import TELEGRAM_BOT_DEFAULTS

from app.bootstrap import build_container_from_env
from app.bot.telegram.handlers.admin import build_admin_router
from app.bot.telegram.handlers.buyout import build_buyout_router
from app.bot.telegram.handlers.profile import build_profile_router
from app.bot.telegram.handlers.questions import build_questions_router
from app.bot.telegram.handlers.start import build_start_router
from app.services.admin_tools_service import BackupService, NotificationSettingsStore, run_periodic_backup_loop
from app.services.error_notifier import ErrorNotifier
from app.core.process_lock import ProcessLock, ProcessLockError


logger = logging.getLogger(__name__)


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    lock = ProcessLock("/tmp/cargo55-telegram-polling.lock")
    try:
        lock.acquire()
    except ProcessLockError:
        logger.error("Another Telegram polling instance is already running.")
        return

    container = await build_container_from_env()
    bot = Bot(
        token=container.settings.telegram.bot_token,
        default=TELEGRAM_BOT_DEFAULTS,
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(build_start_router(container))
    dispatcher.include_router(build_questions_router(container))
    dispatcher.include_router(build_buyout_router(container))
    dispatcher.include_router(build_admin_router(container))
    dispatcher.include_router(build_profile_router(container))

    backup_service = BackupService(
        database_dsn=container.settings.database.dsn,
        profile_repo=container.profile_repo,
        buyout_repo=container.buyout_repo,
    )
    notification_settings_store = NotificationSettingsStore(container.settings.database.dsn)
    error_notifier = ErrorNotifier(container.settings)
    periodic_backup_task = _create_guarded_task(
        "periodic_backup",
        run_periodic_backup_loop(
            backup_service=backup_service,
            bot=bot,
            target_chat_id=container.settings.telegram.backup_chat_id or container.settings.telegram.main_admin_id,
            target_thread_id=container.settings.telegram.backup_topic_id,
            notification_settings_store=notification_settings_store,
        ),
        error_notifier,
    )
    logger.info("Telegram bot (standalone) started")
    try:
        await dispatcher.start_polling(bot)
    except Exception as exc:
        await error_notifier.notify_exception("telegram_standalone_polling", exc)
        raise
    finally:
        periodic_backup_task.cancel()
        lock.release()


def main() -> None:
    asyncio.run(run())


def _create_guarded_task(name: str, coro, notifier: ErrorNotifier) -> asyncio.Task:
    task = asyncio.create_task(coro)

    def _done_callback(done_task: asyncio.Task) -> None:
        try:
            exc = done_task.exception()
        except asyncio.CancelledError:
            return
        if exc is None:
            return
        asyncio.create_task(notifier.notify_exception(name, exc))

    task.add_done_callback(_done_callback)
    return task


if __name__ == "__main__":
    main()
