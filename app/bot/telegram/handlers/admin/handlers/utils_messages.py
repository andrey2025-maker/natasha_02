from __future__ import annotations

import re

import aiohttp
from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from app.bot.telegram.callbacks import CallbackAuthError, CallbackCodec
from app.bot.telegram.fsm_utils import (
    admin_utils_has_waiter,
    fsm_prompt,
    is_cancel_command,
    is_navigation_command,
)
from app.bot.telegram.handlers.admin.all_helpers import *
from app.bot.telegram.handlers.admin.context import AdminContext
from app.bot.telegram.handlers.content_utils_admin import (
    SCREEN_EDIT_MEDIA as CONTENT_UTILS_EDIT_MEDIA,
    SCREEN_EDIT_MENU as CONTENT_UTILS_EDIT_MENU,
    handle_content_utils_callback,
    refresh_content_utils_panel,
    reset_content_utils_state,
    try_handle_content_utils_text,
)
from app.bot.telegram.handlers.faq_admin import (
    SCREEN_CONTENT,
    SCREEN_EDIT_MEDIA,
    handle_faq_admin_callback,
    open_faq_admin_panel,
    refresh_faq_admin_panel,
    reset_faq_admin_state,
    try_handle_faq_admin_text,
)
from app.bot.telegram.handlers.questions_topic import ensure_dialog_topic_for_telegram_user
from app.bot.telegram.keyboards.profile import main_menu_keyboard
from app.core.container import AppContainer
from app.domain.enums import DialogState, OrderStatus, Platform
from app.domain.models import OutboundMessage, UserProfile
from app.services.admin_tools_service import (
    count_targets_for_platform,
    parse_codes,
    send_stored_media_to_telegram,
)
from app.services.dialog_topic_profile_sync import refresh_dialog_topic_profile

def register_utils_messages(router: Router, ctx: AdminContext) -> None:
    container = ctx.container
    callback_codec = ctx.callback_codec
    payment_store = ctx.payment_store
    payment_target_store = ctx.payment_target_store
    notification_settings_store = ctx.notification_settings_store
    prohibited_store = ctx.prohibited_store
    admin_access_store = ctx.admin_access_store
    block_reason_store = ctx.block_reason_store
    profile_comment_store = ctx.profile_comment_store
    faq_media_store = ctx.faq_media_store
    group_topics_store = ctx.group_topics_store
    topic_dialog_store = ctx.topic_dialog_store
    delivery_store = ctx.delivery_store
    contacts_store = ctx.contacts_store
    backup_service = ctx.backup_service

    async def _ensure_admin(message: Message) -> bool:
        return await ctx.ensure_admin(message)

    @router.message(F.text == "Бэкапы")
    async def admin_backups(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        enabled = await backup_service.auto_backup_enabled()
        target_chat_id, target_topic_id = await backup_service.get_backup_target()
        target_hint = "по умолчанию из .env"
        if target_chat_id:
            target_hint = f"chat_id={target_chat_id}, topic_id={target_topic_id or '—'}"
        await message.answer(
            "🗂 Бэкапы: выгрузка БД/Excel и переключение авто-бэкапа каждые 4 часа.",
            parse_mode="HTML",
            reply_markup=_backup_keyboard(message.from_user.id, callback_codec, enabled),
        )
        await message.answer(f"Текущая цель авто-бэкапов: {target_hint}")

    @router.message(F.text == "Группа")
    async def admin_group(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        target_chat_id, target_topic_id = await group_topics_store.get_tg_topic("logs")
        _, payment_topic_id = await group_topics_store.get_tg_topic("payment")
        _, questions_topic_id = await group_topics_store.get_tg_topic("questions")
        _, buyout_topic_id = await group_topics_store.get_tg_topic("buyout")
        vk_logs_peer_id = await group_topics_store.get_vk_logs_peer_id()
        await message.answer(
            "🛰 Техническая группа для авто-бэкапов.\n"
            f"Сейчас: chat_id={target_chat_id or 'не задан'}, logs={target_topic_id or '—'}, "
            f"payment={payment_topic_id or '—'}, questions={questions_topic_id or '—'}, "
            f"buyout={buyout_topic_id or '—'}\n"
            f"VK logs peer_id={vk_logs_peer_id or 'не задан'}\n\n"
            "Нажмите «Задать группу» и отправьте chat_id, например:\n"
            "<code>-1001234567890</code>\n\n"
            "После сохранения бот автоматически создаст темы: логи, оплата, вопросы, Выкупы.",
            parse_mode="HTML",
        )

    @router.message(F.text == "Уведомления")
    async def admin_group_notifications(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        settings = await notification_settings_store.get_settings()
        await message.answer(
            _notification_settings_text(settings),
            reply_markup=_notifications_keyboard(message.from_user.id, callback_codec, settings),
        )

    @router.message(F.text == "Задать группу")
    async def admin_group_set(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        state["awaiting_backup_target"] = True
        state["awaiting_payment_text"] = False
        state["awaiting_payment_media"] = False
        state["awaiting_payment_review_target"] = False
        state["awaiting_prohibited_text"] = False
        state["awaiting_prohibited_media"] = False
        await _save_admin_utils_state(container, session, state)
        await message.answer(fsm_prompt("Отправьте chat_id группы, например: -1001234567890"))

    @router.message(F.text == "Создать темы")
    async def admin_group_create_topics(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        chat_id, _ = await group_topics_store.get_tg_topic("logs")
        if not chat_id:
            await message.answer("Сначала задайте группу через «Задать группу».")
            return
        topics = await _create_required_group_topics(
            bot=message.bot,
            chat_id=int(chat_id),
            group_topics_store=group_topics_store,
            backup_service=backup_service,
            payment_target_store=payment_target_store,
        )
        if topics is None:
            await message.answer(
                "Не удалось создать темы. Проверьте, что это форум-группа и у бота есть права управления темами."
            )
            return
        logs_topic_id, payment_topic_id, questions_topic_id, buyout_topic_id = topics
        created_count, existed_count, failed_count = await _provision_topics_for_existing_telegram_profiles(
            bot=message.bot,
            chat_id=int(chat_id),
            container=container,
            group_topics_store=group_topics_store,
            topic_dialog_store=topic_dialog_store,
        )
        await message.answer(
            "Темы созданы:\n"
            f"- логи: {logs_topic_id}\n"
            f"- оплата: {payment_topic_id}\n"
            f"- вопросы: {questions_topic_id}\n"
            f"- Выкупы: {buyout_topic_id}\n\n"
            "Диалоги пользователей:\n"
            f"- создано тем: {created_count}\n"
            f"- уже существовали: {existed_count}\n"
            f"- ошибок создания: {failed_count}"
        )

    @router.message(F.text == "Создать VK логи")
    async def admin_group_create_vk_logs(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if container.settings.vk is None:
            await message.answer("VK не настроен.")
            return
        peer_id = await _vk_create_logs_chat(container.settings.vk.bot_token)
        if not peer_id:
            await message.answer(
                "Не удалось создать беседу VK автоматически. Проверьте права токена сообщества на сообщения."
            )
            return
        await group_topics_store.set_vk_logs_peer_id(peer_id)
        await message.answer(f"VK-беседа для логов создана: peer_id={peer_id}")

    @router.message(F.text == "Сбросить группу")
    async def admin_group_reset(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        await backup_service.clear_backup_target()
        await group_topics_store.clear_tg()
        await message.answer("Группа и темы сброшены.")

    @router.message(F.text == "Оплаты группа")
    async def admin_payment_group(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        target_chat_id, target_topic_id = await payment_target_store.get_target()
        await message.answer(
            "💳 Группа/тема для заявок на проверку оплаты.\n"
            f"Сейчас: chat_id={target_chat_id or 'не задан'}, topic_id={target_topic_id or '—'}\n\n"
            "Нажмите «Задать оплаты группу» и отправьте:\n"
            "<code>-1001234567890</code>\n"
            "или\n"
            "<code>-1001234567890 42</code> (с topic_id)",
            parse_mode="HTML",
        )

    @router.message(F.text == "Задать оплаты группу")
    async def admin_payment_group_set(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        state["awaiting_payment_review_target"] = True
        state["awaiting_backup_target"] = False
        state["awaiting_payment_text"] = False
        state["awaiting_payment_media"] = False
        state["awaiting_prohibited_text"] = False
        state["awaiting_prohibited_media"] = False
        await _save_admin_utils_state(container, session, state)
        await message.answer(fsm_prompt("Отправьте chat_id [topic_id], например: -1001234567890 42"))

    @router.message(F.text == "Сбросить оплаты группу")
    async def admin_payment_group_reset(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        await payment_target_store.clear_target()
        await message.answer("Цель заявок на проверку оплаты сброшена.")

    @router.message(F.text == "Коды")
    async def admin_codes(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        state["awaiting_codes_add"] = False
        state["awaiting_codes_remove"] = False
        await _save_admin_utils_state(container, session, state)
        reserved = await container.code_reserve_repo.list_reserved()
        preview = ", ".join(reserved[:40]) if reserved else "пусто"
        if len(reserved) > 40:
            preview += ", ..."
        await message.answer(
            "🔐 Резерв кодов:\n"
            f"{preview}\n\n"
            "Команды: «Коды добавить», «Коды удалить».",
        )

    @router.message(F.text == "Коды добавить")
    async def admin_codes_add_start(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        _reset_admin_utils_waiters(state)
        state["awaiting_codes_add"] = True
        await _save_admin_utils_state(container, session, state)
        await message.answer(
            "Отправьте коды для добавления в резерв.\n"
            "Формат: `001, 002, 016` или по одному в строке.",
            parse_mode="Markdown",
        )

    @router.message(F.text == "Коды удалить")
    async def admin_codes_remove_start(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        _reset_admin_utils_waiters(state)
        state["awaiting_codes_remove"] = True
        await _save_admin_utils_state(container, session, state)
        await message.answer(
            "Отправьте коды для удаления из резерва.\n"
            "Формат: `001, 002, 016` или по одному в строке.",
            parse_mode="Markdown",
        )

    @router.message(F.text == "Оплата")
    async def admin_payment(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        text = await payment_store.get_text()
        media_items = await payment_store.get_media_items()
        media_line = f"Медиа: {len(media_items)}"
        await message.answer(
            "💸 Текущая инструкция оплаты:\n\n"
            f"{text}\n\n"
            f"{media_line}\n"
            f"{_media_items_summary(media_items)}\n\n"
            "Команды: «Ред. оплата текст», «Ред. оплата медиа», «Очистить медиа оплаты».\n"
            "Удаление одного файла: «Удалить медиа &lt;номер&gt;».",
        )
        for media in media_items:
            await send_stored_media_to_telegram(message.bot, message.chat.id, media)

    @router.message(F.text == "Ред. оплата текст")
    async def admin_payment_edit_text(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        state["awaiting_payment_text"] = True
        state["awaiting_payment_media"] = False
        state["awaiting_backup_target"] = False
        state["awaiting_payment_review_target"] = False
        state["awaiting_prohibited_text"] = False
        state["awaiting_prohibited_media"] = False
        await _save_admin_utils_state(container, session, state)
        await message.answer("Отправьте новый текст инструкции по оплате одним сообщением.")

    @router.message(F.text == "Ред. оплата медиа")
    async def admin_payment_edit_media(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        state["awaiting_payment_media"] = True
        state["awaiting_payment_text"] = False
        state["awaiting_backup_target"] = False
        state["awaiting_payment_review_target"] = False
        state["awaiting_prohibited_text"] = False
        state["awaiting_prohibited_media"] = False
        await _save_admin_utils_state(container, session, state)
        await message.answer(
            "Отправляйте медиа по одному (фото/видео/gif/документ), можно несколько и разных типов.\n"
            "Когда закончите, отправьте: Готово медиа."
        )

    @router.message(F.text == "Очистить медиа оплаты")
    async def admin_payment_clear_media(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        await payment_store.clear_media()
        await message.answer("Медиа-инструкция оплаты очищена.")

    @router.message(F.text == "Контент")
    async def admin_content(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        await message.answer(
            "🧩 Управление контентом.\n"
            "Раздел: «Доставка контент».\n"
            "Контакты и Запрещенка — в «Утилиты»."
        )

    @router.message(F.text == "Доставка контент")
    async def admin_delivery_content(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        text = await delivery_store.get_text()
        media_items = await delivery_store.get_media_items()
        await message.answer(
            "🚚 Контент «Как работает доставка»:\n\n"
            f"{text}\n\n"
            f"Медиа: {len(media_items)}\n"
            f"{_media_items_summary(media_items)}\n\n"
            "Команды: «Ред. доставка текст», «Ред. доставка медиа», «Очистить медиа доставка».\n"
            "Удаление одного файла: «Удалить медиа &lt;номер&gt;».",
        )
        for media in media_items:
            await send_stored_media_to_telegram(message.bot, message.chat.id, media)

    @router.message(F.text == "Ред. доставка текст")
    async def admin_delivery_edit_text(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        _reset_admin_utils_waiters(state)
        state["awaiting_delivery_text"] = True
        await _save_admin_utils_state(container, session, state)
        await message.answer("Отправьте текст для раздела «Как работает доставка».")

    @router.message(F.text == "Ред. доставка медиа")
    async def admin_delivery_edit_media(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        _reset_admin_utils_waiters(state)
        state["awaiting_delivery_media"] = True
        await _save_admin_utils_state(container, session, state)
        await message.answer(
            "Отправляйте медиа для раздела «Как работает доставка», можно несколько и разных типов.\n"
            "Когда закончите, отправьте: Готово медиа."
        )

    @router.message(F.text == "Очистить медиа доставка")
    async def admin_delivery_clear_media(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        await delivery_store.clear_media()
        await message.answer("Медиа для доставки очищено.")

    @router.message(F.text == "Готово медиа")
    async def admin_media_done(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        if not any(
            [
                state.get("awaiting_payment_media"),
                state.get("awaiting_delivery_media"),
                state.get("awaiting_faq_media_section_id"),
            ]
        ):
            await message.answer("Режим добавления медиа сейчас не активен.")
            return
        if (
            state.get("awaiting_faq_media_section_id")
            and str(state.get("faq_admin_screen") or "") == SCREEN_EDIT_MEDIA
        ):
            section_id = int(state["awaiting_faq_media_section_id"])
            state["faq_admin_screen"] = SCREEN_CONTENT
            state["faq_admin_target_section_id"] = section_id
            state["awaiting_faq_media_section_id"] = None
            await _save_admin_utils_state(container, session, state)
            await refresh_faq_admin_panel(
                message=message,
                container=container,
                codec=callback_codec,
                user_id=message.from_user.id,
                utils_state=state,
                faq_media_store=faq_media_store,
            )
            await message.answer("Сохранено")
            return
        state["awaiting_payment_media"] = False
        state["awaiting_delivery_media"] = False
        state["awaiting_faq_media_section_id"] = None
        await _save_admin_utils_state(container, session, state)
        await message.answer("Добавление медиа завершено.")
