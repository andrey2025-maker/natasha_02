from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.texts import messages as msg
from app.bot.telegram.keyboards.main_menu import main_menu_keyboard
from app.core.container import AppContainer
from app.domain.enums import Platform


def build_start_router(container: AppContainer) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def start_handler(message: Message) -> None:
        if not message.from_user:
            return
        user_key = f"tg:{message.from_user.id}"
        if not container.rate_limiter.allow_request(user_key, message.text):
            return
        profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, message.from_user.id)
        if profile and profile.is_blocked_by_admin:
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return

        is_admin = await container.admin_service.is_admin(message.from_user.id)
        await message.answer(msg.welcome_text(), reply_markup=main_menu_keyboard(include_admin=is_admin))

    return router
