from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.telegram.handler_session import handler_data, resolve_user_session
from app.bot.telegram.fsm_utils import is_cancel_command
from app.bot.telegram.handlers.admin import admin_session_has_pending, clear_admin_input_states
from app.bot.texts import messages as msg
from app.bot.telegram.keyboards.main_menu import main_menu_keyboard
from app.core.container import AppContainer
from app.domain.enums import DialogState, Platform


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

    @router.message(F.text.func(lambda text: is_cancel_command(text)))
    async def cancel_fsm_handler(message: Message, user_session=None) -> None:
        if not message.from_user:
            return
        session = await resolve_user_session(
            handler_data(user_session),
            container,
            Platform.TELEGRAM,
            message.from_user.id,
        )
        if session is None:
            return
        is_admin = await container.admin_service.is_admin(message.from_user.id)
        had_input = session.state != DialogState.IDLE
        if is_admin and admin_session_has_pending(session):
            await clear_admin_input_states(container, session)
            had_input = True
        if session.state != DialogState.IDLE:
            await container.profile_flow.cancel_to_idle(session)
            had_input = True
        if not had_input:
            await message.answer("Сейчас нет активного ввода для отмены.")
            return
        await message.answer(
            "Ввод отменён.",
            reply_markup=main_menu_keyboard(include_admin=is_admin),
        )

    return router
