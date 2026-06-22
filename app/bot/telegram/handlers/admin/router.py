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


def build_admin_router(container: AppContainer) -> Router:
    router = Router()
    ctx = AdminContext.from_container(container)

    register_menu_messages(router, ctx)
    register_utils_messages(router, ctx)
    register_orders_messages(router, ctx)
    register_media_messages(router, ctx)
    register_text_catchall(router, ctx)
    register_blocks_messages(router, ctx)
    register_admins_messages(router, ctx)
    register_callbacks(router, ctx)

    return router
