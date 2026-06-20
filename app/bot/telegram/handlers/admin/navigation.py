from __future__ import annotations

from aiogram.types import Message

from app.bot.telegram.callbacks import CallbackCodec
from app.core.container import AppContainer
from app.domain.enums import Platform

async def _open_user_profile_from_admin(message: Message, container: AppContainer, codec: CallbackCodec) -> None:
    from app.bot.telegram.keyboards.profile import profile_menu_keyboard

    if not message.from_user:
        return
    session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
    response = await container.profile_flow.show_profile_menu(session, other_platform_label="ВК")
    await message.answer(
        response.text,
        parse_mode="HTML",
        reply_markup=profile_menu_keyboard("ВК", message.from_user.id, codec, profile=response.profile),
    )
