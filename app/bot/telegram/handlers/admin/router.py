from __future__ import annotations

from aiogram import Router

from app.bot.telegram.handlers.admin.context import AdminContext
from app.bot.telegram.handlers.admin.handlers.admins_messages import register_admins_messages
from app.bot.telegram.handlers.admin.handlers.blocks_messages import register_blocks_messages
from app.bot.telegram.handlers.admin.handlers.callbacks import register_callbacks
from app.bot.telegram.handlers.admin.handlers.media_messages import register_media_messages
from app.bot.telegram.handlers.admin.handlers.menu_messages import register_menu_messages
from app.bot.telegram.handlers.admin.handlers.orders_messages import register_orders_messages
from app.bot.telegram.handlers.admin.handlers.text_catchall import register_text_catchall
from app.bot.telegram.handlers.admin.handlers.utils_messages import register_utils_messages
from app.core.container import AppContainer


def _admin_ctx(container: AppContainer) -> AdminContext:
    return AdminContext.from_container(container)


def build_admin_handlers_router(container: AppContainer) -> Router:
    """Кнопки и callback админки — без catch-all, можно ставить до user-роутеров."""
    router = Router()
    ctx = _admin_ctx(container)

    register_menu_messages(router, ctx)
    register_utils_messages(router, ctx)
    register_orders_messages(router, ctx)
    register_blocks_messages(router, ctx)
    register_admins_messages(router, ctx)
    register_callbacks(router, ctx)

    return router


def build_admin_input_router(container: AppContainer) -> Router:
    """Текст/медиа в режиме ввода админки — только в конце цепочки роутеров."""
    router = Router()
    ctx = _admin_ctx(container)

    register_text_catchall(router, ctx)
    register_media_messages(router, ctx)

    return router


def build_admin_router(container: AppContainer) -> Router:
    router = Router()
    router.include_router(build_admin_handlers_router(container))
    router.include_router(build_admin_input_router(container))
    return router
