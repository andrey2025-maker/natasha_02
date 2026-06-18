from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.bootstrap import build_container_from_env
from app.bot.vk.app import run_vk_bot, run_vk_outbox_worker
from app.services.error_notifier import ErrorNotifier


logger = logging.getLogger(__name__)


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    container = await build_container_from_env()
    error_notifier = ErrorNotifier(container.settings)
    logger.info("VK bot (standalone) started")
    try:
        await asyncio.gather(
            run_vk_bot(container),
            run_vk_outbox_worker(container),
        )
    except Exception as exc:
        await error_notifier.notify_exception("vk_standalone", exc)
        raise


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
