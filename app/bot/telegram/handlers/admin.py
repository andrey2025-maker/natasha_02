from __future__ import annotations

import aiohttp
from html import escape
import re

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
from app.bot.telegram.keyboards.profile import admin_menu_keyboard, main_menu_keyboard
from app.core.container import AppContainer
from app.domain.enums import OrderStatus, Platform
from app.domain.models import OutboundMessage, UserProfile
from app.services.admin_tools_service import (
    AdminProfileCommentStore,
    AdminPanelAccessStore,
    BackupService,
    BlockReasonStore,
    FaqMediaStore,
    GroupTopicsStore,
    NotificationSettingsStore,
    PaymentReviewTargetStore,
    PaymentTextStore,
    ProhibitedGoodsStore,
    StaticContentStore,
    count_targets_for_platform,
    parse_codes,
    send_stored_media_to_telegram,
)


def build_admin_router(container: AppContainer) -> Router:
    router = Router()
    callback_codec = CallbackCodec(container.callback_signer)
    payment_store = PaymentTextStore(container.settings.database.dsn)
    payment_target_store = PaymentReviewTargetStore(container.settings.database.dsn)
    notification_settings_store = NotificationSettingsStore(container.settings.database.dsn)
    prohibited_store = ProhibitedGoodsStore(container.settings.database.dsn)
    admin_access_store = AdminPanelAccessStore(container.settings.database.dsn)
    block_reason_store = BlockReasonStore(container.settings.database.dsn)
    profile_comment_store = AdminProfileCommentStore(container.settings.database.dsn)
    faq_media_store = FaqMediaStore(container.settings.database.dsn)
    group_topics_store = GroupTopicsStore(container.settings.database.dsn)
    delivery_store = StaticContentStore(
        database_dsn=container.settings.database.dsn,
        key="delivery_info",
        default_text="Раздел о доставке пока не заполнен.",
    )
    contacts_store = StaticContentStore(
        database_dsn=container.settings.database.dsn,
        key="contacts_info",
        default_text="Раздел контактов пока не заполнен.",
    )
    backup_service = BackupService(
        database_dsn=container.settings.database.dsn,
        profile_repo=container.profile_repo,
        buyout_repo=container.buyout_repo,
    )

    async def _ensure_admin(message: Message) -> bool:
        if not message.from_user:
            return False
        return await container.admin_service.is_admin(message.from_user.id)

    @router.message(F.text.in_({"Админ", "🛠 Админ", "🛠️ Админ"}))
    async def admin_root(message: Message) -> None:
        if not await _ensure_admin(message):
            await message.answer("⛔ Доступ к админ-панели только у администраторов.")
            return
        user_id = message.from_user.id
        is_main = user_id == container.settings.telegram.main_admin_id
        await message.answer(
            "Админ-панель.\n"
            "Разделы: Профили, Блокировки, Вопросы, Список админов.\n"
            "Обновление статуса заказа: order status &lt;номер&gt; &lt;статус&gt; | &lt;комментарий&gt;\n"
            "Нижняя клавиатура остается постоянной, команды админки выбирайте кнопками в сообщениях или текстом.",
        )

    @router.message(F.text == "Назад")
    async def admin_back(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer("Главное меню")

    @router.message(F.text == "Профили")
    async def admin_profiles(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        utils_state = _get_admin_utils_state(session)
        utils_state["awaiting_profile_search_query"] = False
        utils_state["profile_search_mode"] = None
        await _save_admin_utils_state(container, session, utils_state)
        await _send_profiles_page(
            message,
            user_id=message.from_user.id,
            page=1,
            container=container,
            codec=callback_codec,
        )

    @router.message(F.text == "Заказы")
    async def admin_orders(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer(
            "📦 Раздел заказов.\n"
            "Используйте команды: «Выкупы» или «Самовыкуп».",
        )

    @router.message(F.text == "Выкупы")
    async def admin_orders_buyout(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_orders_state(session)
        state["page"] = 1
        await _save_admin_orders_state(container, session, state)
        await _send_orders_panel(message, container, callback_codec, message.from_user.id, state)

    @router.message(F.text == "Самовыкуп")
    async def admin_orders_self_buyout(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer(
            "Пока не сделано, Мать Китайчат не объяснила всю суть PRO-CARGO и 1999CARGO! "
            "А так понимание как сюда засунуть пикалку заказов есть, нужно лишь больше информации "
            "для @andreyhggh о взаимодействия с платформами PRO-CARGO и 1999CARGO!"
        )

    @router.message(F.text == "Статистика")
    async def admin_stats(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        text = await container.stats_service.build_overview_text()
        await message.answer(text, parse_mode="HTML")

    @router.message(F.text == "Рассылка")
    async def admin_broadcast(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer(
            "Выберите аудиторию для рассылки, затем отправьте текст или одно медиа с подписью.",
            reply_markup=_broadcast_keyboard(message.from_user.id, callback_codec),
        )

    @router.message(F.text == "Утилиты")
    async def admin_utils(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer(
            "🧰 Утилиты админки.\n"
            "Разделы: «Бэкапы», «Коды», «Группа», «Оплата», «Оплаты группа».",
        )

    @router.message(F.text == "Бэкапы")
    async def admin_backups(message: Message) -> None:
        if not await _ensure_admin(message):
            return
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
            return
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
            "Нажмите «Задать группу» и отправьте:\n"
            "<code>-1001234567890</code>\n"
            "или\n"
            "<code>-1001234567890 42</code> (с topic_id)",
            parse_mode="HTML",
        )

    @router.message(F.text == "Уведомления")
    async def admin_group_notifications(message: Message) -> None:
        if not await _ensure_admin(message):
            return
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
            return
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
        await message.answer("Отправьте chat_id [topic_id], например: -1001234567890 42")

    @router.message(F.text == "Создать темы")
    async def admin_group_create_topics(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        chat_id, _ = await group_topics_store.get_tg_topic("logs")
        if not chat_id:
            await message.answer("Сначала задайте группу через «Задать группу».")
            return
        try:
            topic_logs = await message.bot.create_forum_topic(chat_id=chat_id, name="логи")
            topic_payment = await message.bot.create_forum_topic(chat_id=chat_id, name="оплата")
            topic_questions = await message.bot.create_forum_topic(chat_id=chat_id, name="вопросы")
            topic_buyout = await message.bot.create_forum_topic(chat_id=chat_id, name="выкуп")
        except Exception:
            await message.answer(
                "Не удалось создать темы. Проверьте, что это форум-группа и у бота есть права управления темами."
            )
            return
        await group_topics_store.set_tg_topics(
            logs_topic_id=int(topic_logs.message_thread_id),
            payment_topic_id=int(topic_payment.message_thread_id),
            questions_topic_id=int(topic_questions.message_thread_id),
            buyout_topic_id=int(topic_buyout.message_thread_id),
        )
        await backup_service.set_backup_target(chat_id=chat_id, topic_id=int(topic_logs.message_thread_id))
        await payment_target_store.set_target(chat_id=chat_id, topic_id=int(topic_payment.message_thread_id))
        await message.answer(
            "Темы созданы:\n"
            f"- логи: {topic_logs.message_thread_id}\n"
            f"- оплата: {topic_payment.message_thread_id}\n"
            f"- вопросы: {topic_questions.message_thread_id}\n"
            f"- выкуп: {topic_buyout.message_thread_id}"
        )

    @router.message(F.text == "Создать VK логи")
    async def admin_group_create_vk_logs(message: Message) -> None:
        if not await _ensure_admin(message):
            return
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
            return
        await backup_service.clear_backup_target()
        await group_topics_store.clear_tg()
        await message.answer("Группа и темы сброшены.")

    @router.message(F.text == "Оплаты группа")
    async def admin_payment_group(message: Message) -> None:
        if not await _ensure_admin(message):
            return
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
            return
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
        await message.answer("Отправьте chat_id [topic_id], например: -1001234567890 42")

    @router.message(F.text == "Сбросить оплаты группу")
    async def admin_payment_group_reset(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await payment_target_store.clear_target()
        await message.answer("Цель заявок на проверку оплаты сброшена.")

    @router.message(F.text == "Коды")
    async def admin_codes(message: Message) -> None:
        if not await _ensure_admin(message):
            return
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
            return
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
            return
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
            return
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
            return
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
            return
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
            return
        await payment_store.clear_media()
        await message.answer("Медиа-инструкция оплаты очищена.")

    @router.message(F.text == "Запрещенка")
    async def admin_prohibited(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        text = await prohibited_store.get_text()
        media_items = await prohibited_store.get_media_items()
        media_line = f"Медиа: {len(media_items)}"
        await message.answer(
            "🚫 Раздел «Запрещенные товары»:\n\n"
            f"{text}\n\n"
            f"{media_line}\n"
            f"{_media_items_summary(media_items)}\n\n"
            "Команды: «Ред. запрещенка текст», «Ред. запрещенка медиа», «Очистить медиа запрещенка».\n"
            "Удаление одного файла: «Удалить медиа &lt;номер&gt;».",
        )
        for media in media_items:
            await send_stored_media_to_telegram(message.bot, message.chat.id, media)

    @router.message(F.text == "Контент")
    async def admin_content(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer("🧩 Управление контентом.\nРазделы: «Доставка контент», «Контакты контент», «Запрещенка».")

    @router.message(F.text == "Доставка контент")
    async def admin_delivery_content(message: Message) -> None:
        if not await _ensure_admin(message):
            return
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

    @router.message(F.text == "Контакты контент")
    async def admin_contacts_content(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        text = await contacts_store.get_text()
        media_items = await contacts_store.get_media_items()
        await message.answer(
            "☎️ Контент «Наши контакты»:\n\n"
            f"{text}\n\n"
            f"Медиа: {len(media_items)}\n"
            f"{_media_items_summary(media_items)}\n\n"
            "Команды: «Ред. контакты текст», «Ред. контакты медиа», «Очистить медиа контакты».\n"
            "Удаление одного файла: «Удалить медиа &lt;номер&gt;».",
        )
        for media in media_items:
            await send_stored_media_to_telegram(message.bot, message.chat.id, media)

    @router.message(F.text == "Ред. запрещенка текст")
    async def admin_prohibited_edit_text(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        _reset_admin_utils_waiters(state)
        state["awaiting_prohibited_text"] = True
        await _save_admin_utils_state(container, session, state)
        await message.answer("Отправьте новый текст для раздела запрещенных товаров.")

    @router.message(F.text == "Ред. запрещенка медиа")
    async def admin_prohibited_edit_media(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        _reset_admin_utils_waiters(state)
        state["awaiting_prohibited_media"] = True
        await _save_admin_utils_state(container, session, state)
        await message.answer(
            "Отправляйте медиа по одному для запрещенки, можно несколько и разных типов.\n"
            "Когда закончите, отправьте: Готово медиа."
        )

    @router.message(F.text == "Очистить медиа запрещенка")
    async def admin_prohibited_clear_media(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await prohibited_store.clear_media()
        await message.answer("Медиа для запрещенки очищено.")

    @router.message(F.text == "Ред. доставка текст")
    async def admin_delivery_edit_text(message: Message) -> None:
        if not await _ensure_admin(message):
            return
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
            return
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
            return
        await delivery_store.clear_media()
        await message.answer("Медиа для доставки очищено.")

    @router.message(F.text == "Ред. контакты текст")
    async def admin_contacts_edit_text(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        _reset_admin_utils_waiters(state)
        state["awaiting_contacts_text"] = True
        await _save_admin_utils_state(container, session, state)
        await message.answer("Отправьте текст для раздела «Наши контакты».")

    @router.message(F.text == "Ред. контакты медиа")
    async def admin_contacts_edit_media(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        _reset_admin_utils_waiters(state)
        state["awaiting_contacts_media"] = True
        await _save_admin_utils_state(container, session, state)
        await message.answer(
            "Отправляйте медиа для раздела «Наши контакты», можно несколько и разных типов.\n"
            "Когда закончите, отправьте: Готово медиа."
        )

    @router.message(F.text == "Готово медиа")
    async def admin_media_done(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        if not any(
            [
                state.get("awaiting_payment_media"),
                state.get("awaiting_prohibited_media"),
                state.get("awaiting_delivery_media"),
                state.get("awaiting_contacts_media"),
                state.get("awaiting_faq_media_section_id"),
            ]
        ):
            await message.answer("Режим добавления медиа сейчас не активен.")
            return
        state["awaiting_payment_media"] = False
        state["awaiting_prohibited_media"] = False
        state["awaiting_delivery_media"] = False
        state["awaiting_contacts_media"] = False
        state["awaiting_faq_media_section_id"] = None
        await _save_admin_utils_state(container, session, state)
        await message.answer("Добавление медиа завершено.")

    @router.message(F.text == "Очистить медиа контакты")
    async def admin_contacts_clear_media(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await contacts_store.clear_media()
        await message.answer("Медиа для контактов очищено.")

    @router.message(F.text.regexp(r"^order\s+status\s+\S+\s+\S+(\s*\|\s*.*)?$"))
    async def admin_order_status(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user or not message.text:
            return
        body = message.text[len("order status ") :].strip()
        note = ""
        if "|" in body:
            left, note = body.split("|", maxsplit=1)
            body = left.strip()
            note = note.strip()
        parts = body.split()
        if len(parts) < 2:
            await message.answer("Формат: order status &lt;номер&gt; &lt;статус&gt; | &lt;комментарий&gt;")
            return
        order_number, status_raw = parts[0], parts[1]
        status = _parse_order_status(status_raw)
        if status is None:
            await message.answer(
                "Неизвестный статус. Пример: order status 001/1P paid | подтверждено"
            )
            return
        updated = await container.order_admin_service.set_status(
            order_number=order_number,
            new_status=status,
            changed_by_user_id=message.from_user.id,
            note=note,
            platform=Platform.TELEGRAM,
        )
        if not updated:
            await message.answer("Заказ не найден.")
            return
        await _notify_order_status_change(
            trigger_message=message,
            container=container,
            payment_store=payment_store,
            codec=callback_codec,
            order=updated,
            new_status=status,
            note=note,
        )
        await message.answer(
            f"Статус обновлен: {updated.order_number} → {_order_status_name(updated.status)}"
        )

    @router.message()
    async def admin_order_edit_input(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user or not message.text:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        broadcast_state = _get_admin_broadcast_state(session)
        utils_state = _get_admin_utils_state(session)

        if utils_state.get("awaiting_payment_text"):
            new_text = message.text.strip()
            if not new_text:
                await message.answer("Текст не может быть пустым.")
                return
            await payment_store.save_text(new_text)
            utils_state["awaiting_payment_text"] = False
            utils_state["awaiting_payment_media"] = False
            await _save_admin_utils_state(container, session, utils_state)
            await message.answer("Инструкция по оплате обновлена.")
            return

        if utils_state.get("awaiting_payment_media"):
            handled = await _handle_media_text_command(
                message=message,
                store=payment_store,
                section_name="оплаты",
            )
            if not handled:
                await message.answer(
                    "Сейчас ожидается медиа. Отправьте файл или команду «Удалить медиа &lt;номер&gt;» / «Готово медиа»."
                )
            return

        if utils_state.get("awaiting_delivery_text"):
            new_text = message.text.strip()
            if not new_text:
                await message.answer("Текст не может быть пустым.")
                return
            await delivery_store.save_text(new_text)
            utils_state["awaiting_delivery_text"] = False
            await _save_admin_utils_state(container, session, utils_state)
            await message.answer("Текст доставки обновлен.")
            return

        if utils_state.get("awaiting_contacts_text"):
            new_text = message.text.strip()
            if not new_text:
                await message.answer("Текст не может быть пустым.")
                return
            await contacts_store.save_text(new_text)
            utils_state["awaiting_contacts_text"] = False
            await _save_admin_utils_state(container, session, utils_state)
            await message.answer("Текст контактов обновлен.")
            return

        if utils_state.get("awaiting_prohibited_text"):
            new_text = message.text.strip()
            if not new_text:
                await message.answer("Текст не может быть пустым.")
                return
            await prohibited_store.save_text(new_text)
            utils_state["awaiting_prohibited_text"] = False
            utils_state["awaiting_prohibited_media"] = False
            await _save_admin_utils_state(container, session, utils_state)
            await message.answer("Текст запрещенки обновлен.")
            return

        if utils_state.get("awaiting_prohibited_media"):
            handled = await _handle_media_text_command(
                message=message,
                store=prohibited_store,
                section_name="запрещенки",
            )
            if not handled:
                await message.answer(
                    "Сейчас ожидается медиа для запрещенки. Используйте «Удалить медиа &lt;номер&gt;» или «Готово медиа»."
                )
            return

        if utils_state.get("awaiting_delivery_media"):
            handled = await _handle_media_text_command(
                message=message,
                store=delivery_store,
                section_name="доставки",
            )
            if not handled:
                await message.answer(
                    "Сейчас ожидается медиа для раздела доставки. Используйте «Удалить медиа &lt;номер&gt;» или «Готово медиа»."
                )
            return

        if utils_state.get("awaiting_contacts_media"):
            handled = await _handle_media_text_command(
                message=message,
                store=contacts_store,
                section_name="контактов",
            )
            if not handled:
                await message.answer(
                    "Сейчас ожидается медиа для раздела контактов. Используйте «Удалить медиа &lt;номер&gt;» или «Готово медиа»."
                )
            return
        if utils_state.get("awaiting_faq_media_section_id"):
            section_id = int(utils_state.get("awaiting_faq_media_section_id"))
            handled = await _handle_media_text_command(
                message=message,
                store=faq_media_store,
                section_name=f"FAQ {section_id}",
                section_id=section_id,
            )
            if not handled:
                await message.answer(
                    "Сейчас ожидается медиа FAQ. Используйте «Удалить медиа &lt;номер&gt;» или «Готово медиа»."
                )
            return

        if utils_state.get("awaiting_codes_add"):
            codes = parse_codes(message.text)
            if not codes:
                await message.answer("Не распознаны коды. Пример: 001, 002, 016")
                return
            added = await container.code_reserve_repo.add_codes(codes)
            utils_state["awaiting_codes_add"] = False
            await _save_admin_utils_state(container, session, utils_state)
            await message.answer(
                "Добавлено в резерв: "
                + (", ".join(added) if added else "ничего (коды заняты или уже в резерве)"),
            )
            return

        if utils_state.get("awaiting_codes_remove"):
            codes = parse_codes(message.text)
            if not codes:
                await message.answer("Не распознаны коды. Пример: 001, 002, 016")
                return
            removed = await container.code_reserve_repo.remove_codes(codes)
            utils_state["awaiting_codes_remove"] = False
            await _save_admin_utils_state(container, session, utils_state)
            await message.answer(
                "Удалено из резерва: " + (", ".join(removed) if removed else "ничего не удалено"),
            )
            return

        profile_comment_code = str(utils_state.get("awaiting_profile_comment_code") or "").strip()
        if profile_comment_code:
            text = message.text.strip()
            if text == "-":
                text = ""
            await profile_comment_store.set_comment(profile_comment_code, text)
            utils_state["awaiting_profile_comment_code"] = None
            await _save_admin_utils_state(container, session, utils_state)
            await message.answer("Комментарий профиля обновлен.")
            return

        if utils_state.get("awaiting_admin_add_id"):
            if not message.text.strip().isdigit():
                await message.answer("Нужен числовой Telegram ID.")
                return
            target_id = int(message.text.strip())
            ok = await container.admin_service.add_admin(message.from_user.id, target_id)
            utils_state["awaiting_admin_add_id"] = False
            await _save_admin_utils_state(container, session, utils_state)
            await message.answer("Админ добавлен." if ok else "Только главный админ может добавлять.")
            if ok:
                try:
                    await message.bot.send_message(
                        chat_id=target_id,
                        text="Вам выданы права администратора. Кнопка «Админ» доступна в меню.",
                    )
                except Exception:
                    pass
            return

        if utils_state.get("awaiting_admin_add_code"):
            code = message.text.strip().zfill(3)
            profile = await container.admin_service.get_profile(code)
            if not profile or not profile.telegram_user_id:
                await message.answer("Профиль не найден или в нем нет Telegram ID.")
                return
            ok = await container.admin_service.add_admin(message.from_user.id, int(profile.telegram_user_id))
            utils_state["awaiting_admin_add_code"] = False
            await _save_admin_utils_state(container, session, utils_state)
            await message.answer("Админ добавлен." if ok else "Только главный админ может добавлять.")
            if ok:
                try:
                    await message.bot.send_message(
                        chat_id=int(profile.telegram_user_id),
                        text="Вам выданы права администратора. Кнопка «Админ» доступна в меню.",
                    )
                except Exception:
                    pass
            return

        faq_action = str(utils_state.get("awaiting_faq_action") or "")
        if faq_action in {"add", "title", "text"}:
            if "|" not in message.text:
                await message.answer("Неверный формат, используйте разделитель `|`.", parse_mode="Markdown")
                return
            left, right = [part.strip() for part in message.text.split("|", maxsplit=1)]
            try:
                parsed_id = int(left) if left != "root" else None
            except ValueError:
                await message.answer("ID раздела должен быть числом или `root`.", parse_mode="Markdown")
                return
            if faq_action == "add":
                parent_id = parsed_id
                created = await container.faq_service.create_section(parent_id=parent_id, title=right)
                utils_state["awaiting_faq_action"] = None
                await _save_admin_utils_state(container, session, utils_state)
                await message.answer(f"Раздел создан: {created.id} — {created.title}")
                return
            if faq_action == "title":
                if parsed_id is None:
                    await message.answer("Для редактирования нужен конкретный ID раздела.")
                    return
                updated = await container.faq_service.update_section_title(parsed_id, right)
                utils_state["awaiting_faq_action"] = None
                await _save_admin_utils_state(container, session, utils_state)
                if not updated:
                    await message.answer("Раздел не найден.")
                    return
                await message.answer(f"Заголовок обновлен: {updated.id} — {updated.title}")
                return
            if faq_action == "text":
                if parsed_id is None:
                    await message.answer("Для редактирования нужен конкретный ID раздела.")
                    return
                updated = await container.faq_service.update_section_text(parsed_id, right)
                utils_state["awaiting_faq_action"] = None
                await _save_admin_utils_state(container, session, utils_state)
                if not updated:
                    await message.answer("Раздел не найден.")
                    return
                await message.answer(f"Текст обновлен для раздела {updated.id}.")
                return

        if utils_state.get("awaiting_profile_search_query"):
            mode = str(utils_state.get("profile_search_mode") or "").strip().lower()
            query = message.text.strip()
            if mode not in {"code", "name", "id", "tag"}:
                await message.answer("Сначала выберите режим поиска в разделе «Профили».")
                return
            results = await container.admin_service.search_profiles(by=mode, query=query, limit=30)
            utils_state["awaiting_profile_search_query"] = False
            utils_state["profile_search_mode"] = None
            await _save_admin_utils_state(container, session, utils_state)
            if not results:
                await message.answer("Поиск не дал результатов.")
                return
            lines = ["Результаты поиска:"]
            lines.extend([f"- {item.code} — {item.name or 'Без имени'}" for item in results])
            await message.answer(
                "\n".join(lines),
                reply_markup=_profiles_search_results_keyboard(
                    user_id=message.from_user.id,
                    codec=callback_codec,
                    profiles=results,
                ),
            )
            return

        if utils_state.get("awaiting_block_search_query"):
            mode = str(utils_state.get("block_search_mode") or "").strip().lower()
            operation = str(utils_state.get("block_operation") or "").strip().lower()
            query = message.text.strip()
            if mode not in {"code", "name", "id", "tag"} or operation not in {"block", "unblock"}:
                await message.answer("Сначала выберите режим блокировки в разделе «Блокировки».")
                return
            results = await container.admin_service.search_profiles(by=mode, query=query, limit=30)
            if operation == "block":
                results = [item for item in results if not item.is_blocked_by_admin]
            else:
                results = [item for item in results if item.is_blocked_by_admin]
            utils_state["awaiting_block_search_query"] = False
            utils_state["block_search_mode"] = None
            utils_state["block_operation"] = None
            await _save_admin_utils_state(container, session, utils_state)
            if not results:
                await message.answer("Подходящих профилей не найдено.")
                return
            op_title = "блокировки" if operation == "block" else "разблокировки"
            await message.answer(
                f"Результаты для {op_title}:",
                reply_markup=_block_pick_keyboard(
                    user_id=message.from_user.id,
                    codec=callback_codec,
                    profiles=results,
                    operation=operation,
                ),
            )
            return

        block_reason_code = str(utils_state.get("awaiting_block_reason_for_code") or "").strip()
        if block_reason_code:
            reason = message.text.strip()
            ok = await container.admin_service.set_block_status(block_reason_code, blocked=True)
            if not ok:
                utils_state["awaiting_block_reason_for_code"] = None
                await _save_admin_utils_state(container, session, utils_state)
                await message.answer("Профиль не найден.")
                return
            if reason == "-":
                reason = ""
            if reason:
                await block_reason_store.set_reason(block_reason_code, reason)
            else:
                await block_reason_store.clear_reason(block_reason_code)
            utils_state["awaiting_block_reason_for_code"] = None
            await _save_admin_utils_state(container, session, utils_state)
            await message.answer("Пользователь заблокирован.")
            return

        if utils_state.get("awaiting_backup_target"):
            parts = message.text.split()
            if len(parts) not in {1, 2}:
                await message.answer("Формат: chat_id [topic_id], например: -1001234567890 42")
                return
            try:
                chat_id = int(parts[0])
                topic_id = int(parts[1]) if len(parts) == 2 else None
            except ValueError:
                await message.answer("chat_id и topic_id должны быть числами.")
                return
            await backup_service.set_backup_target(chat_id=chat_id, topic_id=topic_id)
            await group_topics_store.set_tg_chat_id(chat_id)
            if topic_id:
                await group_topics_store.set_tg_topics(
                    logs_topic_id=topic_id,
                    payment_topic_id=topic_id,
                    questions_topic_id=topic_id,
                    buyout_topic_id=topic_id,
                )
            utils_state["awaiting_backup_target"] = False
            await _save_admin_utils_state(container, session, utils_state)
            await message.answer(
                f"Цель авто-бэкапов обновлена: chat_id={chat_id}, topic_id={topic_id or '—'}"
            )
            return

        if utils_state.get("awaiting_payment_review_target"):
            parts = message.text.split()
            if len(parts) not in {1, 2}:
                await message.answer("Формат: chat_id [topic_id], например: -1001234567890 42")
                return
            try:
                chat_id = int(parts[0])
                topic_id = int(parts[1]) if len(parts) == 2 else None
            except ValueError:
                await message.answer("chat_id и topic_id должны быть числами.")
                return
            await payment_target_store.set_target(chat_id=chat_id, topic_id=topic_id)
            utils_state["awaiting_payment_review_target"] = False
            await _save_admin_utils_state(container, session, utils_state)
            await message.answer(
                f"Цель оплат обновлена: chat_id={chat_id}, topic_id={topic_id or '—'}"
            )
            return

        if broadcast_state.get("awaiting_payload"):
            audience = str(broadcast_state.get("audience") or "")
            text = message.text.strip()
            if audience not in {"all", "active", "inactive"}:
                await message.answer("Сначала выберите аудиторию в разделе «Рассылка».")
                return
            if not text:
                await message.answer("Текст рассылки не может быть пустым.")
                return
            tg_sent, tg_failed, vk_enqueued = await _dispatch_broadcast_text(
                message,
                container=container,
                backup_service=backup_service,
                audience=audience,
                text=text,
            )
            broadcast_state["awaiting_payload"] = False
            broadcast_state["audience"] = None
            await _save_admin_broadcast_state(container, session, broadcast_state)
            await message.answer(
                "Рассылка поставлена в работу.\n"
                f"TG отправлено: {tg_sent}\n"
                f"TG ошибки: {tg_failed}\n"
                f"VK в очередь: {vk_enqueued}"
            )
            return

        if message.text.lower().startswith("codes "):
            payload = message.text[6:].strip()
            if payload.lower().startswith("add "):
                codes = parse_codes(payload[4:])
                if not codes:
                    await message.answer("Не распознаны коды. Пример: codes add 001, 002")
                    return
                added = await container.code_reserve_repo.add_codes(codes)
                await message.answer(
                    "Добавлено в резерв: "
                    + (", ".join(added) if added else "ничего (коды заняты или уже в резерве)")
                )
                return
            if payload.lower().startswith("del "):
                codes = parse_codes(payload[4:])
                if not codes:
                    await message.answer("Не распознаны коды. Пример: codes del 001, 002")
                    return
                removed = await container.code_reserve_repo.remove_codes(codes)
                await message.answer(
                    "Удалено из резерва: " + (", ".join(removed) if removed else "ничего не удалено")
                )
                return
            await message.answer("Используйте: codes add ... или codes del ...")
            return

        state = _get_admin_orders_state(session)
        edit_order = state.get("edit_order")
        edit_field = state.get("edit_field")
        bulk_field = state.get("bulk_field")

        if bulk_field and state.get("selected"):
            ok, normalized_or_error = _validate_field_input(str(bulk_field), message.text)
            if not ok:
                await message.answer(str(normalized_or_error))
                return
            try:
                changed = await container.order_admin_service.bulk_update_field(
                    order_numbers=[str(item) for item in state.get("selected", [])],
                    field_name=str(bulk_field),
                    raw_value=str(normalized_or_error),
                )
            except Exception:
                await message.answer("Не удалось применить массовое обновление. Проверьте формат значения.")
                return
            state["bulk_field"] = None
            await _save_admin_orders_state(container, session, state)
            await message.answer(
                f"Массово обновлено `{_field_title(str(bulk_field))}` у заказов: {changed}",
                parse_mode="Markdown",
            )
            return

        if not edit_order or not edit_field:
            raise SkipHandler
        ok, normalized_or_error = _validate_field_input(str(edit_field), message.text)
        if not ok:
            await message.answer(str(normalized_or_error))
            return
        state["pending_field"] = str(edit_field)
        state["pending_value"] = str(normalized_or_error)
        state["edit_field"] = None
        await _save_admin_orders_state(container, session, state)
        confirm_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Сохранить",
                        callback_data=callback_codec.encode("admin:orders:confirm_apply", message.from_user.id),
                    ),
                    InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data=callback_codec.encode("admin:orders:confirm_cancel", message.from_user.id),
                    ),
                ]
            ]
        )
        await message.answer(
            "Подтвердите изменение:\n"
            f"Заказ: <b>{_h(edit_order)}</b>\n"
            f"Поле: <b>{_h(_field_title(str(state.get('pending_field') or '')))}</b>\n"
            f"Новое значение: <code>{_h(state.get('pending_value'))}</code>",
            parse_mode="HTML",
            reply_markup=confirm_keyboard,
        )

    @router.message(F.text == "Блокировки")
    async def admin_blocked(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        utils_state = _get_admin_utils_state(session)
        utils_state["awaiting_block_search_query"] = False
        utils_state["block_search_mode"] = None
        utils_state["block_operation"] = None
        await _save_admin_utils_state(container, session, utils_state)
        await message.answer(
            "Блокировки: выберите раздел.",
            reply_markup=_blocks_menu_keyboard(message.from_user.id, callback_codec),
        )

    @router.message(F.photo | F.video | F.animation | F.document)
    async def admin_broadcast_media_input(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        utils_state = _get_admin_utils_state(session)
        if utils_state.get("awaiting_payment_media"):
            media_type = ""
            file_id = ""
            if message.photo:
                media_type = "photo"
                file_id = message.photo[-1].file_id
            elif message.video:
                media_type = "video"
                file_id = message.video.file_id
            elif message.animation:
                media_type = "animation"
                file_id = message.animation.file_id
            elif message.document:
                media_type = "document"
                file_id = message.document.file_id
            if media_type and file_id:
                archive_chat_id, archive_topic_id, archive_message_id = await _archive_media_in_group_topic(
                    message=message,
                    group_topics_store=group_topics_store,
                    label="payment_media",
                )
                vk_attachment = await _sync_vk_attachment_from_tg(
                    message=message,
                    container=container,
                    media_type=media_type,
                    file_id=file_id,
                )
                await payment_store.save_media(
                    media_type=media_type,
                    file_id=file_id,
                    caption=message.caption or "",
                    vk_attachment=vk_attachment,
                    storage_chat_id=archive_chat_id,
                    storage_topic_id=archive_topic_id,
                    storage_message_id=archive_message_id,
                )
                await _save_admin_utils_state(container, session, utils_state)
                suffix = " и синхронизировано в VK." if vk_attachment else ". VK синхронизация не выполнена."
                await message.answer("Медиа-инструкция оплаты добавлена" + suffix + " Отправьте ещё или «Готово медиа».")
            return
        if utils_state.get("awaiting_delivery_media"):
            media_type = ""
            file_id = ""
            if message.photo:
                media_type = "photo"
                file_id = message.photo[-1].file_id
            elif message.video:
                media_type = "video"
                file_id = message.video.file_id
            elif message.animation:
                media_type = "animation"
                file_id = message.animation.file_id
            elif message.document:
                media_type = "document"
                file_id = message.document.file_id
            if media_type and file_id:
                archive_chat_id, archive_topic_id, archive_message_id = await _archive_media_in_group_topic(
                    message=message,
                    group_topics_store=group_topics_store,
                    label="delivery_media",
                )
                vk_attachment = await _sync_vk_attachment_from_tg(
                    message=message,
                    container=container,
                    media_type=media_type,
                    file_id=file_id,
                )
                await delivery_store.save_media(
                    media_type=media_type,
                    file_id=file_id,
                    caption=message.caption or "",
                    vk_attachment=vk_attachment,
                    storage_chat_id=archive_chat_id,
                    storage_topic_id=archive_topic_id,
                    storage_message_id=archive_message_id,
                )
                await _save_admin_utils_state(container, session, utils_state)
                suffix = " и синхронизировано в VK." if vk_attachment else ". VK синхронизация не выполнена."
                await message.answer("Медиа доставки добавлено" + suffix + " Отправьте ещё или «Готово медиа».")
            return
        if utils_state.get("awaiting_contacts_media"):
            media_type = ""
            file_id = ""
            if message.photo:
                media_type = "photo"
                file_id = message.photo[-1].file_id
            elif message.video:
                media_type = "video"
                file_id = message.video.file_id
            elif message.animation:
                media_type = "animation"
                file_id = message.animation.file_id
            elif message.document:
                media_type = "document"
                file_id = message.document.file_id
            if media_type and file_id:
                archive_chat_id, archive_topic_id, archive_message_id = await _archive_media_in_group_topic(
                    message=message,
                    group_topics_store=group_topics_store,
                    label="contacts_media",
                )
                vk_attachment = await _sync_vk_attachment_from_tg(
                    message=message,
                    container=container,
                    media_type=media_type,
                    file_id=file_id,
                )
                await contacts_store.save_media(
                    media_type=media_type,
                    file_id=file_id,
                    caption=message.caption or "",
                    vk_attachment=vk_attachment,
                    storage_chat_id=archive_chat_id,
                    storage_topic_id=archive_topic_id,
                    storage_message_id=archive_message_id,
                )
                await _save_admin_utils_state(container, session, utils_state)
                suffix = " и синхронизировано в VK." if vk_attachment else ". VK синхронизация не выполнена."
                await message.answer("Медиа контактов добавлено" + suffix + " Отправьте ещё или «Готово медиа».")
            return
        if utils_state.get("awaiting_prohibited_media"):
            media_type = ""
            file_id = ""
            if message.photo:
                media_type = "photo"
                file_id = message.photo[-1].file_id
            elif message.video:
                media_type = "video"
                file_id = message.video.file_id
            elif message.animation:
                media_type = "animation"
                file_id = message.animation.file_id
            elif message.document:
                media_type = "document"
                file_id = message.document.file_id
            if media_type and file_id:
                archive_chat_id, archive_topic_id, archive_message_id = await _archive_media_in_group_topic(
                    message=message,
                    group_topics_store=group_topics_store,
                    label="prohibited_media",
                )
                vk_attachment = await _sync_vk_attachment_from_tg(
                    message=message,
                    container=container,
                    media_type=media_type,
                    file_id=file_id,
                )
                await prohibited_store.save_media(
                    media_type=media_type,
                    file_id=file_id,
                    caption=message.caption or "",
                    vk_attachment=vk_attachment,
                    storage_chat_id=archive_chat_id,
                    storage_topic_id=archive_topic_id,
                    storage_message_id=archive_message_id,
                )
                await _save_admin_utils_state(container, session, utils_state)
                suffix = " и синхронизировано в VK." if vk_attachment else ". VK синхронизация не выполнена."
                await message.answer("Медиа запрещенки добавлено" + suffix + " Отправьте ещё или «Готово медиа».")
            return
        faq_section_id = utils_state.get("awaiting_faq_media_section_id")
        if faq_section_id:
            media_type = ""
            file_id = ""
            if message.photo:
                media_type = "photo"
                file_id = message.photo[-1].file_id
            elif message.video:
                media_type = "video"
                file_id = message.video.file_id
            elif message.animation:
                media_type = "animation"
                file_id = message.animation.file_id
            elif message.document:
                media_type = "document"
                file_id = message.document.file_id
            if media_type and file_id:
                archive_chat_id, archive_topic_id, archive_message_id = await _archive_media_in_group_topic(
                    message=message,
                    group_topics_store=group_topics_store,
                    label=f"faq_media_{faq_section_id}",
                )
                vk_attachment = await _sync_vk_attachment_from_tg(
                    message=message,
                    container=container,
                    media_type=media_type,
                    file_id=file_id,
                )
                await faq_media_store.save_media(
                    section_id=int(faq_section_id),
                    media_type=media_type,
                    file_id=file_id,
                    caption=message.caption or "",
                    vk_attachment=vk_attachment,
                    storage_chat_id=archive_chat_id,
                    storage_topic_id=archive_topic_id,
                    storage_message_id=archive_message_id,
                )
                await _save_admin_utils_state(container, session, utils_state)
                suffix = " и синхронизировано в VK." if vk_attachment else ". VK синхронизация не выполнена."
                await message.answer(
                    f"FAQ медиа (раздел {faq_section_id}) добавлено{suffix} Отправьте ещё или «Готово медиа»."
                )
            return

        state = _get_admin_broadcast_state(session)
        if not state.get("awaiting_payload"):
            return
        audience = str(state.get("audience") or "")
        if audience not in {"all", "active", "inactive"}:
            await message.answer("Сначала выберите аудиторию в разделе «Рассылка».")
            return

        tg_sent, tg_failed, vk_enqueued = await _dispatch_broadcast_media(
            message,
            container=container,
            backup_service=backup_service,
            audience=audience,
        )
        state["awaiting_payload"] = False
        state["audience"] = None
        await _save_admin_broadcast_state(container, session, state)
        await message.answer(
            "Медиа-рассылка поставлена в работу.\n"
            f"TG отправлено: {tg_sent}\n"
            f"TG ошибки: {tg_failed}\n"
            f"VK в очередь (текст-пояснение): {vk_enqueued}"
        )

    @router.message(F.text == "Список админов")
    async def admin_list(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        is_main = message.from_user.id == container.settings.telegram.main_admin_id
        open_for_all = await admin_access_store.is_open_for_all_admins()
        if not is_main and not open_for_all:
            await message.answer("Раздел доступен только главному админу.")
            return
        admin_ids = await container.admin_service.list_admins()
        lines = [f"- {admin_id}" for admin_id in admin_ids]
        await message.answer(
            "Админы:\n" + "\n".join(lines),
            reply_markup=_admins_access_keyboard(
                user_id=message.from_user.id,
                codec=callback_codec,
                open_for_all=open_for_all,
                is_main=is_main,
                admin_ids=admin_ids,
                main_admin_id=container.settings.telegram.main_admin_id,
            ),
        )

    @router.message(F.text == "Вопросы")
    async def faq_admin_help(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        state["awaiting_faq_action"] = None
        await _save_admin_utils_state(container, session, state)
        await message.answer(
            "📚 Управление FAQ.\n"
            "Команды: «FAQ Добавить», «FAQ Ред. заголовок», «FAQ Ред. текст», "
            "«FAQ Медиа», «FAQ Очистить медиа», «FAQ Показать root».",
        )

    @router.message(F.text == "FAQ Добавить")
    async def faq_add_prompt(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        _reset_admin_utils_waiters(state)
        state["awaiting_faq_action"] = "add"
        await _save_admin_utils_state(container, session, state)
        await message.answer("Формат: root|&lt;parent_id&gt; | &lt;название&gt;")

    @router.message(F.text == "FAQ Ред. заголовок")
    async def faq_title_prompt(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        _reset_admin_utils_waiters(state)
        state["awaiting_faq_action"] = "title"
        await _save_admin_utils_state(container, session, state)
        await message.answer("Формат: &lt;id&gt; | &lt;новый заголовок&gt;")

    @router.message(F.text == "FAQ Ред. текст")
    async def faq_text_prompt(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user:
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        state = _get_admin_utils_state(session)
        _reset_admin_utils_waiters(state)
        state["awaiting_faq_action"] = "text"
        await _save_admin_utils_state(container, session, state)
        await message.answer("Формат: &lt;id&gt; | &lt;новый текст&gt;")

    @router.message(F.text == "FAQ Показать root")
    async def faq_show_root_button(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        items = await container.faq_service.list_children(None)
        if not items:
            await message.answer("FAQ root\nПодразделов нет.")
            return
        rows = [f"{item.id}. {item.title}" for item in items]
        await message.answer("FAQ root\n" + "\n".join(rows))

    @router.message(F.text == "FAQ Медиа")
    async def faq_media_help_button(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer("Формат: faq media &lt;id раздела&gt;")

    @router.message(F.text == "FAQ Очистить медиа")
    async def faq_media_clear_help_button(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer("Формат: faq media clear &lt;id раздела&gt;")

    @router.message(F.text == "Добавить админа")
    async def add_admin_help(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer("Напишите: add_admin &lt;telegram_id&gt;")

    @router.message(F.text == "Удалить админа")
    async def remove_admin_help(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        await message.answer("Напишите: del_admin &lt;telegram_id&gt;")

    @router.message(F.text.regexp(r"^(add_admin|del_admin)\s+\d+$"))
    async def admin_manage(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user or not message.text:
            return
        command, user_id_raw = message.text.split()
        target_id = int(user_id_raw)
        if command == "add_admin":
            ok = await container.admin_service.add_admin(message.from_user.id, target_id)
            await message.answer("Админ добавлен." if ok else "Только главный админ может добавлять.")
            if ok:
                try:
                    await message.bot.send_message(
                        chat_id=target_id,
                        text="Вам выданы права администратора. Кнопка «Админ» доступна в меню.",
                    )
                except Exception:
                    pass
            return
        ok = await container.admin_service.remove_admin(message.from_user.id, target_id)
        await message.answer("Админ удален." if ok else "Только главный админ может удалять.")
        if ok:
            try:
                await message.bot.send_message(
                    chat_id=target_id,
                    text="Права администратора сняты. Хороших заказов!",
                )
            except Exception:
                pass

    @router.message(F.text.regexp(r"^faq\s+show\s+(root|\d+)$"))
    async def faq_show(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.text:
            return
        raw = message.text.split()[-1]
        parent_id = None if raw == "root" else int(raw)
        items = await container.faq_service.list_children(parent_id)
        if parent_id is None:
            title = "FAQ root"
        else:
            section = await container.faq_service.get_section(parent_id)
            title = f"FAQ раздел {parent_id}: {section.title if section else 'не найден'}"
        if not items:
            await message.answer(f"{title}\nПодразделов нет.")
            return
        rows = [f"{item.id}. {item.title}" for item in items]
        await message.answer(f"{title}\n" + "\n".join(rows))

    @router.message(F.text.regexp(r"^faq\s+add\s+(root|\d+)\s*\|\s*.+$"))
    async def faq_add(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.text:
            return
        payload = message.text[len("faq add ") :].strip()
        parent_raw, title = [part.strip() for part in payload.split("|", maxsplit=1)]
        parent_id = None if parent_raw == "root" else int(parent_raw)
        section = await container.faq_service.create_section(parent_id=parent_id, title=title)
        await message.answer(f"Раздел создан: {section.id} — {section.title}")

    @router.message(F.text.regexp(r"^faq\s+title\s+\d+\s*\|\s*.+$"))
    async def faq_title(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.text:
            return
        payload = message.text[len("faq title ") :].strip()
        section_id_raw, new_title = [part.strip() for part in payload.split("|", maxsplit=1)]
        updated = await container.faq_service.update_section_title(int(section_id_raw), new_title)
        if not updated:
            await message.answer("Раздел не найден.")
            return
        await message.answer(f"Заголовок обновлен: {updated.id} — {updated.title}")

    @router.message(F.text.regexp(r"^faq\s+text\s+\d+\s*\|\s*.+$"))
    async def faq_text(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.text:
            return
        payload = message.text[len("faq text ") :].strip()
        section_id_raw, content = [part.strip() for part in payload.split("|", maxsplit=1)]
        updated = await container.faq_service.update_section_text(int(section_id_raw), content)
        if not updated:
            await message.answer("Раздел не найден.")
            return
        await message.answer(f"Текст обновлен для раздела {updated.id}.")

    @router.message(F.text.regexp(r"^faq\s+media\s+\d+$"))
    async def faq_media_start(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.from_user or not message.text:
            return
        section_id = int(message.text.split()[-1])
        section = await container.faq_service.get_section(section_id)
        if not section:
            await message.answer("Раздел не найден.")
            return
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        utils_state = _get_admin_utils_state(session)
        _reset_admin_utils_waiters(utils_state)
        utils_state["awaiting_faq_media_section_id"] = section_id
        await _save_admin_utils_state(container, session, utils_state)
        await message.answer(
            f"Режим медиа FAQ для раздела {section_id} ({section.title}).\n"
            "Отправьте файлы. Для завершения: «Готово медиа».\n"
            "Удаление по индексу: faq media del &lt;id&gt; &lt;index&gt;."
        )

    @router.message(F.text.regexp(r"^faq\s+media\s+clear\s+\d+$"))
    async def faq_media_clear(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.text:
            return
        section_id = int(message.text.split()[-1])
        await faq_media_store.clear_media(section_id)
        await message.answer(f"FAQ медиа очищено для раздела {section_id}.")

    @router.message(F.text.regexp(r"^faq\s+media\s+del\s+\d+\s+\d+$"))
    async def faq_media_delete(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.text:
            return
        _, _, _, section_raw, index_raw = message.text.split()
        ok = await faq_media_store.remove_media_at(int(section_raw), int(index_raw))
        await message.answer("Удалено." if ok else "Неверный индекс.")

    @router.message(F.text.regexp(r"^код\s+\d+$"))
    async def admin_profile_by_code(message: Message) -> None:
        if not await _ensure_admin(message):
            return
        if not message.text:
            return
        code = message.text.split(maxsplit=1)[1].zfill(3)
        profile = await container.admin_service.get_profile(code)
        if not profile:
            await message.answer("Профиль не найден.")
            return
        block_reason = await block_reason_store.get_reason(profile.code)
        profile_comment = await profile_comment_store.get_comment(profile.code)
        await message.answer(
            _profile_details(profile, block_reason=block_reason, profile_comment=profile_comment),
            parse_mode="HTML",
            reply_markup=_profile_actions_keyboard(profile, message.from_user.id, callback_codec),
        )

    @router.callback_query()
    async def admin_callbacks(callback: CallbackQuery) -> None:
        if not callback.data or not callback.from_user or not callback.message:
            return
        if not await container.admin_service.is_admin(callback.from_user.id):
            raise SkipHandler
        try:
            action = callback_codec.decode(callback.data, callback.from_user.id)
        except CallbackAuthError:
            raise SkipHandler

        if action.startswith("admin:broadcast:"):
            audience = action.split(":")[-1]
            if audience not in {"all", "active", "inactive"}:
                await callback.answer("Неизвестная аудитория", show_alert=True)
                return
            session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
            state = _get_admin_broadcast_state(session)
            state["awaiting_payload"] = True
            state["audience"] = audience
            await _save_admin_broadcast_state(container, session, state)
            profiles = await backup_service.pick_profiles_for_broadcast(audience)
            tg_count = count_targets_for_platform(profiles, Platform.TELEGRAM)
            vk_count = count_targets_for_platform(profiles, Platform.VK)
            await callback.answer()
            await callback.message.answer(
                "Аудитория выбрана.\n"
                f"Получатели: TG {tg_count}, VK {vk_count}\n"
                "Теперь отправьте текст рассылки одним сообщением."
            )
            return

        if action.startswith("admin:backup:auto:"):
            mode = action.split(":")[-1]
            enabled = mode == "on"
            await backup_service.set_auto_backup_enabled(enabled)
            await callback.answer("Авто-бэкап обновлен")
            await callback.message.edit_reply_markup(
                reply_markup=_backup_keyboard(callback.from_user.id, callback_codec, enabled)
            )
            return

        if action.startswith("admin:notify:toggle:"):
            key = action.split(":")[-1]
            settings = await notification_settings_store.toggle(key)
            await callback.answer("Настройки обновлены")
            await callback.message.edit_text(
                _notification_settings_text(settings),
                reply_markup=_notifications_keyboard(callback.from_user.id, callback_codec, settings),
            )
            return

        if action == "admin:admins:toggle_access":
            if callback.from_user.id != container.settings.telegram.main_admin_id:
                await callback.answer("Только главный админ", show_alert=True)
                return
            new_value = await admin_access_store.toggle()
            admin_ids = await container.admin_service.list_admins()
            lines = [f"- {admin_id}" for admin_id in admin_ids]
            await callback.answer("Доступ обновлен")
            await callback.message.edit_text(
                "Админы:\n" + "\n".join(lines),
                reply_markup=_admins_access_keyboard(
                    user_id=callback.from_user.id,
                    codec=callback_codec,
                    open_for_all=new_value,
                    is_main=True,
                    admin_ids=admin_ids,
                    main_admin_id=container.settings.telegram.main_admin_id,
                ),
            )
            return

        if action == "admin:admins:add_id":
            if callback.from_user.id != container.settings.telegram.main_admin_id:
                await callback.answer("Только главный админ", show_alert=True)
                return
            session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
            utils_state = _get_admin_utils_state(session)
            _reset_admin_utils_waiters(utils_state)
            utils_state["awaiting_admin_add_id"] = True
            await _save_admin_utils_state(container, session, utils_state)
            await callback.answer()
            await callback.message.answer("Введите Telegram ID пользователя для добавления в админы.")
            return

        if action == "admin:admins:add_code":
            if callback.from_user.id != container.settings.telegram.main_admin_id:
                await callback.answer("Только главный админ", show_alert=True)
                return
            session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
            utils_state = _get_admin_utils_state(session)
            _reset_admin_utils_waiters(utils_state)
            utils_state["awaiting_admin_add_code"] = True
            await _save_admin_utils_state(container, session, utils_state)
            await callback.answer()
            await callback.message.answer("Введите код профиля (например, 001) для добавления в админы.")
            return

        if action.startswith("admin:admins:remove:"):
            if callback.from_user.id != container.settings.telegram.main_admin_id:
                await callback.answer("Только главный админ", show_alert=True)
                return
            target_id = int(action.split(":")[-1])
            ok = await container.admin_service.remove_admin(callback.from_user.id, target_id)
            if ok:
                try:
                    await callback.bot.send_message(
                        chat_id=target_id,
                        text="Права администратора сняты. Хороших заказов!",
                    )
                except Exception:
                    pass
            admin_ids = await container.admin_service.list_admins()
            lines = [f"- {admin_id}" for admin_id in admin_ids]
            await callback.answer("Админ удален" if ok else "Не удалось удалить", show_alert=not ok)
            await callback.message.edit_text(
                "Админы:\n" + "\n".join(lines),
                reply_markup=_admins_access_keyboard(
                    user_id=callback.from_user.id,
                    codec=callback_codec,
                    open_for_all=await admin_access_store.is_open_for_all_admins(),
                    is_main=True,
                    admin_ids=admin_ids,
                    main_admin_id=container.settings.telegram.main_admin_id,
                ),
            )
            return

        if action == "admin:backup:db":
            await callback.answer("Готовлю SQL-бэкап...")
            file_path = await backup_service.create_db_backup()
            disable_notification = await notification_settings_store.should_disable_notification("bot")
            await callback.message.answer_document(
                FSInputFile(str(file_path)),
                caption=f"Бэкап БД: {file_path.name}",
                disable_notification=disable_notification,
            )
            return

        if action == "admin:backup:excel":
            await callback.answer("Готовлю CSV (Excel) ...")
            file_path = await backup_service.create_excel_backup()
            disable_notification = await notification_settings_store.should_disable_notification("bot")
            await callback.message.answer_document(
                FSInputFile(str(file_path)),
                caption=f"Excel-бэкап: {file_path.name}",
                disable_notification=disable_notification,
            )
            return

        if action.startswith("admin:profiles:"):
            payload = action.split(":", maxsplit=2)[2]
            if payload.startswith("page:"):
                page = int(payload.split(":")[1])
                await callback.answer()
                await _send_profiles_page(
                    callback.message,
                    user_id=callback.from_user.id,
                    page=page,
                    container=container,
                    codec=callback_codec,
                )
                return
            if payload == "search_menu":
                await callback.answer()
                await callback.message.answer(
                    "Выберите, по чему искать профиль:",
                    reply_markup=_profiles_search_mode_keyboard(callback.from_user.id, callback_codec),
                )
                return
            if payload.startswith("search:"):
                mode = payload.split(":", maxsplit=1)[1]
                if mode not in {"code", "name", "id", "tag"}:
                    await callback.answer("Неизвестный режим", show_alert=True)
                    return
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                utils_state = _get_admin_utils_state(session)
                utils_state["awaiting_profile_search_query"] = True
                utils_state["profile_search_mode"] = mode
                await _save_admin_utils_state(container, session, utils_state)
                mode_title = {"code": "Код", "name": "Имя", "id": "ID", "tag": "Тэг"}[mode]
                await callback.answer()
                await callback.message.answer(f"Введите запрос для поиска по полю «{mode_title}».")
                return

        if action.startswith("admin:blocks:"):
            payload = action.split(":", maxsplit=2)[2]
            if payload.startswith("show_blocked"):
                page = _parse_blocks_page(payload, default=1)
                blocked = await _collect_profiles(container, predicate=lambda item: item.is_blocked_by_admin, limit=500)
                reasons = await block_reason_store.list_reasons()
                await callback.answer()
                if not blocked:
                    await callback.message.answer("Заблокированных админом пока нет.")
                else:
                    text, markup = _render_blocked_page(callback.from_user.id, callback_codec, blocked, page, reasons)
                    await callback.message.answer(text, reply_markup=markup)
                return
            if payload.startswith("show_unsubscribed"):
                page = _parse_blocks_page(payload, default=1)
                unsubscribed = await _collect_profiles(container, predicate=lambda item: item.blocked_bot, limit=500)
                await callback.answer()
                if not unsubscribed:
                    await callback.message.answer("Отписанных (заблокировали бота) пока нет.")
                else:
                    text, markup = _render_unsubscribed_page(callback.from_user.id, callback_codec, unsubscribed, page)
                    await callback.message.answer(text, reply_markup=markup)
                return
            if payload == "start_block":
                await callback.answer()
                await callback.message.answer(
                    "Выберите поле для поиска профиля, которого нужно заблокировать:",
                    reply_markup=_block_search_mode_keyboard(callback.from_user.id, callback_codec),
                )
                return
            if payload == "start_unblock":
                blocked = await _collect_profiles(container, predicate=lambda item: item.is_blocked_by_admin, limit=90)
                await callback.answer()
                if not blocked:
                    await callback.message.answer("Заблокированных админом пока нет.")
                    return
                await callback.message.answer(
                    "Выберите профиль для разблокировки:",
                    reply_markup=_block_pick_keyboard(
                        callback.from_user.id,
                        callback_codec,
                        blocked,
                        operation="unblock",
                    ),
                )
                return
            if payload.startswith("search:"):
                mode = payload.split(":", maxsplit=1)[1]
                if mode not in {"code", "name", "id", "tag"}:
                    await callback.answer("Неизвестный режим", show_alert=True)
                    return
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                utils_state = _get_admin_utils_state(session)
                utils_state["awaiting_block_search_query"] = True
                utils_state["block_search_mode"] = mode
                utils_state["block_operation"] = "block"
                await _save_admin_utils_state(container, session, utils_state)
                mode_title = {"code": "Код", "name": "Имя", "id": "ID", "tag": "Тэг"}[mode]
                await callback.answer()
                await callback.message.answer(f"Введите запрос для блокировки (поле «{mode_title}»).")
                return

        if action.startswith("admin:blockpick:"):
            _, _, operation, code = action.split(":", maxsplit=3)
            if operation not in {"block", "unblock"}:
                await callback.answer("Некорректная операция", show_alert=True)
                return
            if operation == "block":
                session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
                utils_state = _get_admin_utils_state(session)
                _reset_admin_utils_waiters(utils_state)
                utils_state["awaiting_block_reason_for_code"] = code
                await _save_admin_utils_state(container, session, utils_state)
                await callback.answer()
                await callback.message.answer(
                    f"Введите причину блокировки для кода {code}.\n"
                    "Если причина не нужна, отправьте `-`.",
                    parse_mode="Markdown",
                )
                return
            ok = await container.admin_service.set_block_status(code, blocked=False)
            if ok:
                await block_reason_store.clear_reason(code)
            await callback.answer("Пользователь разблокирован" if ok else "Профиль не найден")
            return

        if action.startswith("admin:profile:view:"):
            code = action.split(":")[-1]
            profile = await container.admin_service.get_profile(code)
            if not profile:
                await callback.answer("Профиль не найден", show_alert=True)
                return
            block_reason = await block_reason_store.get_reason(profile.code)
            profile_comment = await profile_comment_store.get_comment(profile.code)
            await callback.answer()
            await callback.message.answer(
                _profile_details(profile, block_reason=block_reason, profile_comment=profile_comment),
                parse_mode="HTML",
                reply_markup=_profile_actions_keyboard(profile, callback.from_user.id, callback_codec),
            )
            return

        if action.startswith("admin:profile:comment:"):
            code = action.split(":")[-1]
            profile = await container.admin_service.get_profile(code)
            if not profile:
                await callback.answer("Профиль не найден", show_alert=True)
                return
            session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
            utils_state = _get_admin_utils_state(session)
            _reset_admin_utils_waiters(utils_state)
            utils_state["awaiting_profile_comment_code"] = code
            await _save_admin_utils_state(container, session, utils_state)
            current_comment = await profile_comment_store.get_comment(code)
            await callback.answer()
            await callback.message.answer(
                "Введите комментарий для профиля.\n"
                "Чтобы очистить, отправьте `-`.\n\n"
                f"Текущий: {current_comment or '—'}",
                parse_mode="Markdown",
            )
            return

        if action.startswith("admin:orders:"):
            session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, callback.from_user.id)
            state = _get_admin_orders_state(session)
            payload = action.split(":", maxsplit=2)[2]

            if payload.startswith("page:"):
                page = max(1, int(payload.split(":")[1]))
                state["page"] = page
                await _save_admin_orders_state(container, session, state)
            elif payload.startswith("toggle:"):
                order_number = payload.split(":", maxsplit=1)[1]
                selected = set(state.get("selected", []))
                if order_number in selected:
                    selected.remove(order_number)
                else:
                    selected.add(order_number)
                state["selected"] = sorted(selected)
                await _save_admin_orders_state(container, session, state)
            elif payload.startswith("set_status:"):
                status_raw = payload.split(":", maxsplit=1)[1]
                status = _parse_order_status(status_raw)
                if status is None:
                    await callback.answer("Неизвестный статус", show_alert=True)
                    return
                selected = list(state.get("selected", []))
                if not selected:
                    await callback.answer("Выберите хотя бы один заказ", show_alert=True)
                    return
                changed = 0
                for order_number in selected:
                    updated = await container.order_admin_service.set_status(
                        order_number=order_number,
                        new_status=status,
                        changed_by_user_id=callback.from_user.id,
                        note="bulk update",
                        platform=Platform.TELEGRAM,
                    )
                    if updated:
                        changed += 1
                        await _notify_order_status_change(
                            trigger_message=callback.message,
                            container=container,
                            payment_store=payment_store,
                            codec=callback_codec,
                            order=updated,
                            new_status=status,
                            note="bulk update",
                        )
                state["selected"] = []
                await _save_admin_orders_state(container, session, state)
                await callback.answer(f"Обновлено: {changed}")
            elif payload == "edit":
                selected = list(state.get("selected", []))
                if len(selected) != 1:
                    await callback.answer("Для редактирования выберите ровно 1 заказ", show_alert=True)
                    return
                state["edit_order"] = selected[0]
                state["edit_field"] = None
                state["bulk_field"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer()
                await _send_order_edit_panel(
                    callback.message,
                    container=container,
                    user_id=callback.from_user.id,
                    codec=callback_codec,
                    state=state,
                    edit=True,
                )
                return
            elif payload.startswith("edit_field:"):
                field = payload.split(":", maxsplit=1)[1]
                if not state.get("edit_order"):
                    await callback.answer("Сначала выберите заказ", show_alert=True)
                    return
                state["edit_field"] = field
                state["pending_field"] = None
                state["pending_value"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer()
                order = await container.order_admin_service.get_order(str(state.get("edit_order")))
                current_value = _order_field_value(order, field) if order else "—"
                await callback.message.answer(
                    f"Введите новое значение для поля: {_field_title(field)}\n"
                    f"Текущее: <code>{_h(current_value)}</code>",
                    parse_mode="HTML",
                )
                return
            elif payload == "confirm_apply":
                order_number = str(state.get("edit_order") or "")
                field = str(state.get("pending_field") or "")
                value = str(state.get("pending_value") or "")
                if not order_number or not field:
                    await callback.answer("Нет подготовленного изменения", show_alert=True)
                    return
                try:
                    updated = await container.order_admin_service.update_order_field(
                        order_number=order_number,
                        field_name=field,
                        raw_value=value,
                    )
                except Exception:
                    await callback.answer("Не удалось сохранить", show_alert=True)
                    return
                if not updated:
                    await callback.answer("Заказ не найден", show_alert=True)
                    return
                state["pending_field"] = None
                state["pending_value"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer("Изменение сохранено")
                await _send_order_edit_panel(
                    callback.message,
                    container=container,
                    user_id=callback.from_user.id,
                    codec=callback_codec,
                    state=state,
                    edit=True,
                )
                return
            elif payload == "confirm_cancel":
                state["pending_field"] = None
                state["pending_value"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer("Изменение отменено")
                await _send_order_edit_panel(
                    callback.message,
                    container=container,
                    user_id=callback.from_user.id,
                    codec=callback_codec,
                    state=state,
                    edit=True,
                )
                return
            elif payload.startswith("bulk_field:"):
                field = payload.split(":", maxsplit=1)[1]
                selected = list(state.get("selected", []))
                if not selected:
                    await callback.answer("Выберите заказы для массового редактирования", show_alert=True)
                    return
                state["bulk_field"] = field
                state["edit_field"] = None
                state["edit_order"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer()
                await callback.message.answer(
                    f"Введите новое значение для поля `{_field_title(field)}`. "
                    f"Будет применено к {len(selected)} заказам.",
                    parse_mode="Markdown",
                )
                return
            elif payload == "back_list":
                state["edit_order"] = None
                state["edit_field"] = None
                state["bulk_field"] = None
                state["pending_field"] = None
                state["pending_value"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer()
            elif payload == "clear":
                state["selected"] = []
                state["edit_order"] = None
                state["edit_field"] = None
                state["bulk_field"] = None
                state["pending_field"] = None
                state["pending_value"] = None
                await _save_admin_orders_state(container, session, state)
                await callback.answer("Выбор очищен")

            await _send_orders_panel(callback.message, container, callback_codec, callback.from_user.id, state, edit=True)
            return

        if action.startswith("admin:block:") or action.startswith("admin:unblock:"):
            _, op, code = action.split(":")
            blocked = op == "block"
            ok = await container.admin_service.set_block_status(code, blocked=blocked)
            if ok and not blocked:
                await block_reason_store.clear_reason(code)
            await callback.answer("Статус обновлен" if ok else "Профиль не найден")
            if not ok:
                return
            profile = await container.admin_service.get_profile(code)
            if not profile:
                return
            block_reason = await block_reason_store.get_reason(profile.code)
            profile_comment = await profile_comment_store.get_comment(profile.code)
            await callback.message.edit_text(
                _profile_details(profile, block_reason=block_reason, profile_comment=profile_comment),
                parse_mode="HTML",
                reply_markup=_profile_actions_keyboard(profile, callback.from_user.id, callback_codec),
            )
            return

        raise SkipHandler

    return router


async def _send_profiles_page(
    message: Message,
    user_id: int,
    page: int,
    container: AppContainer,
    codec: CallbackCodec,
) -> None:
    safe_page = max(1, page)
    items = await container.admin_service.list_profiles(page=safe_page, page_size=9)
    if not items and safe_page > 1:
        safe_page -= 1
        items = await container.admin_service.list_profiles(page=safe_page, page_size=9)
    if not items:
        await message.answer("Профилей пока нет.")
        return
    rows = [
        f"{idx}. {_profile_state_emoji(p)} {_h(p.code)} — {_h(p.name or 'Без имени')}"
        for idx, p in enumerate(items, start=1 + (safe_page - 1) * 9)
    ]
    text = "Профили (напишите `код 001` для просмотра):\n" + "\n".join(rows)
    await message.answer(text, parse_mode="HTML", reply_markup=_profiles_pagination(user_id, safe_page, codec, items))


def _profiles_pagination(user_id: int, page: int, codec: CallbackCodec, items: list[UserProfile]):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows: list[list[InlineKeyboardButton]] = []
    code_buttons: list[InlineKeyboardButton] = []
    for item in items:
        code_buttons.append(
            InlineKeyboardButton(
                text=item.code,
                callback_data=codec.encode(f"admin:profile:view:{item.code}", user_id),
            )
        )
        if len(code_buttons) == 3:
            rows.append(code_buttons)
            code_buttons = []
    if code_buttons:
        rows.append(code_buttons)
    rows.append(
        [
            InlineKeyboardButton(
                text="🔎 Поиск",
                callback_data=codec.encode("admin:profiles:search_menu", user_id),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️",
                callback_data=codec.encode(f"admin:profiles:page:{max(1, page - 1)}", user_id),
            ),
            InlineKeyboardButton(
                text="➡️",
                callback_data=codec.encode(f"admin:profiles:page:{page + 1}", user_id),
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _profiles_search_mode_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Код",
                    callback_data=codec.encode("admin:profiles:search:code", user_id),
                ),
                InlineKeyboardButton(
                    text="Имя",
                    callback_data=codec.encode("admin:profiles:search:name", user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="ID",
                    callback_data=codec.encode("admin:profiles:search:id", user_id),
                ),
                InlineKeyboardButton(
                    text="Тэг",
                    callback_data=codec.encode("admin:profiles:search:tag", user_id),
                ),
            ],
        ]
    )


def _profiles_search_results_keyboard(
    user_id: int,
    codec: CallbackCodec,
    profiles: list[UserProfile],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for profile in profiles[:30]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=profile.code,
                    callback_data=codec.encode(f"admin:profile:view:{profile.code}", user_id),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _blocks_menu_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Заблокированные",
                    callback_data=codec.encode("admin:blocks:show_blocked:1", user_id),
                ),
                InlineKeyboardButton(
                    text="Отписанные",
                    callback_data=codec.encode("admin:blocks:show_unsubscribed:1", user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Заблокировать",
                    callback_data=codec.encode("admin:blocks:start_block", user_id),
                ),
                InlineKeyboardButton(
                    text="Разблокировать",
                    callback_data=codec.encode("admin:blocks:start_unblock", user_id),
                ),
            ],
        ]
    )


def _parse_blocks_page(payload: str, default: int = 1) -> int:
    parts = payload.split(":")
    if len(parts) < 2:
        return default
    try:
        return max(1, int(parts[-1]))
    except ValueError:
        return default


def _render_blocked_page(
    user_id: int,
    codec: CallbackCodec,
    blocked: list[UserProfile],
    page: int,
    reasons: dict[str, str],
) -> tuple[str, InlineKeyboardMarkup]:
    page_size = 10
    total = len(blocked)
    total_pages = max(1, (total + page_size - 1) // page_size)
    safe_page = min(max(1, page), total_pages)
    start = (safe_page - 1) * page_size
    items = blocked[start : start + page_size]
    lines = [f"Заблокированные (стр. {safe_page}/{total_pages}):"]
    for idx, item in enumerate(items, start=1 + start):
        reason = reasons.get(item.code, "")
        if reason:
            lines.append(f"{idx}. {item.code} — {item.name or 'Без имени'} | Причина: {reason}")
        else:
            lines.append(f"{idx}. {item.code} — {item.name or 'Без имени'}")
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔓 {item.code}",
                    callback_data=codec.encode(f"admin:blockpick:unblock:{item.code}", user_id),
                ),
                InlineKeyboardButton(
                    text=f"👤 {item.code}",
                    callback_data=codec.encode(f"admin:profile:view:{item.code}", user_id),
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️",
                callback_data=codec.encode(f"admin:blocks:show_blocked:{max(1, safe_page - 1)}", user_id),
            ),
            InlineKeyboardButton(
                text="➡️",
                callback_data=codec.encode(f"admin:blocks:show_blocked:{min(total_pages, safe_page + 1)}", user_id),
            ),
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _render_unsubscribed_page(
    user_id: int,
    codec: CallbackCodec,
    unsubscribed: list[UserProfile],
    page: int,
) -> tuple[str, InlineKeyboardMarkup]:
    page_size = 10
    total = len(unsubscribed)
    total_pages = max(1, (total + page_size - 1) // page_size)
    safe_page = min(max(1, page), total_pages)
    start = (safe_page - 1) * page_size
    items = unsubscribed[start : start + page_size]
    lines = [f"Отписанные (стр. {safe_page}/{total_pages}):"]
    for idx, item in enumerate(items, start=1 + start):
        lines.append(f"{idx}. {item.code} — {item.name or 'Без имени'}")
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👤 {item.code}",
                    callback_data=codec.encode(f"admin:profile:view:{item.code}", user_id),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️",
                callback_data=codec.encode(f"admin:blocks:show_unsubscribed:{max(1, safe_page - 1)}", user_id),
            ),
            InlineKeyboardButton(
                text="➡️",
                callback_data=codec.encode(
                    f"admin:blocks:show_unsubscribed:{min(total_pages, safe_page + 1)}",
                    user_id,
                ),
            ),
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _block_search_mode_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    prefix = f"admin:blocks:search"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Код",
                    callback_data=codec.encode(f"{prefix}:code", user_id),
                ),
                InlineKeyboardButton(
                    text="Имя",
                    callback_data=codec.encode(f"{prefix}:name", user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="ID",
                    callback_data=codec.encode(f"{prefix}:id", user_id),
                ),
                InlineKeyboardButton(
                    text="Тэг",
                    callback_data=codec.encode(f"{prefix}:tag", user_id),
                ),
            ],
        ]
    )


def _block_pick_keyboard(
    user_id: int,
    codec: CallbackCodec,
    profiles: list[UserProfile],
    operation: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in profiles[:30]:
        title = f"{item.code} · {item.name or 'Без имени'}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=codec.encode(f"admin:blockpick:{operation}:{item.code}", user_id),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _collect_profiles(
    container: AppContainer,
    predicate,
    limit: int = 90,
) -> list[UserProfile]:
    page = 1
    page_size = 200
    items: list[UserProfile] = []
    while True:
        chunk = await container.admin_service.list_profiles(page=page, page_size=page_size)
        if not chunk:
            break
        for item in chunk:
            if predicate(item):
                items.append(item)
                if len(items) >= limit:
                    return items
        if len(chunk) < page_size:
            break
        page += 1
    return items


def _profile_state_emoji(profile: UserProfile) -> str:
    if profile.is_blocked_by_admin:
        return "⛔"
    if profile.blocked_bot:
        return "🚫"
    return "✅"


def _block_button(profile: UserProfile, user_id: int, codec: CallbackCodec):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    if profile.is_blocked_by_admin:
        text = "Разблокировать"
        action = f"admin:unblock:{profile.code}"
    else:
        text = "Заблокировать"
        action = f"admin:block:{profile.code}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=codec.encode(action, user_id))],
        ]
    )


def _profile_actions_keyboard(profile: UserProfile, user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    block_kb = _block_button(profile, user_id, codec)
    rows = list(block_kb.inline_keyboard)
    rows.append(
        [
            InlineKeyboardButton(
                text="✏️ Комментарий",
                callback_data=codec.encode(f"admin:profile:comment:{profile.code}", user_id),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _profile_details(
    profile: UserProfile,
    block_reason: str | None = None,
    profile_comment: str | None = None,
) -> str:
    blocked_admin_text = "Да" if profile.is_blocked_by_admin else "Нет"
    blocked_bot_text = "Да" if profile.blocked_bot else "Нет"
    reason_line = block_reason or "—"
    comment_line = profile_comment or "—"
    return (
        f"<b>Имя:</b> {_h(profile.name or '—')}\n"
        f"<b>Код:</b> {_h(profile.code)}\n"
        f"<b>Тел:</b> {_h(profile.phone or '—')}\n"
        f"<b>Город:</b> {_h(profile.city or '—')}\n"
        f"<b>Загран паспорт:</b> {'Да' if profile.has_passport else 'Нет'}\n"
        f"<b>Комментарий:</b> {_h(comment_line)}\n"
        f"<b>TG ID:</b> {_h(profile.telegram_user_id or 'Нет')}\n"
        f"<b>VK ID:</b> {_h(profile.vk_user_id or 'Нет')}\n"
        f"<b>Заблокирован админом:</b> {_h(blocked_admin_text)}\n"
        f"<b>Причина блокировки:</b> {_h(reason_line)}\n"
        f"<b>Отписан от бота:</b> {_h(blocked_bot_text)}\n"
        f"<b>Последняя активность:</b> {_h(profile.last_activity_at.strftime('%d.%m.%Y %H:%M'))}\n"
        f"<b>Дата регистрации:</b> {_h(profile.created_at.strftime('%d.%m.%Y %H:%M'))}"
    )


async def _send_orders_panel(
    message: Message,
    container: AppContainer,
    codec: CallbackCodec,
    user_id: int,
    state: dict,
    edit: bool = False,
) -> None:
    page = int(state.get("page", 1))
    selected = set(state.get("selected", []))
    orders, total = await container.order_admin_service.list_recent_orders(page=page, page_size=9)
    if not orders and page > 1:
        page = page - 1
        state["page"] = page
        orders, total = await container.order_admin_service.list_recent_orders(page=page, page_size=9)
    total_pages = max(1, (total + 8) // 9)

    lines = ["<b>Заказы (массовое обновление)</b>"]
    lines.append(f"Страница {page}/{total_pages}")
    lines.append(f"Выбрано: {len(selected)}")
    lines.append("")
    if not orders:
        lines.append("Заказов пока нет.")
    else:
        for order in orders:
            mark = "✅" if order.order_number in selected else "⬜️"
            lines.append(
                f"{mark} {_h(order.order_number)} — {_h(_order_status_name(order.status))} "
                f"({order.updated_at.strftime('%d.%m.%y')})"
            )

    keyboard = _orders_keyboard(user_id, codec, page, total_pages, orders, selected)
    if edit:
        await message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


def _orders_keyboard(
    user_id: int,
    codec: CallbackCodec,
    page: int,
    total_pages: int,
    orders,
    selected: set[str],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for order in orders:
        mark = "✅" if order.order_number in selected else "⬜️"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {order.order_number}",
                    callback_data=codec.encode(f"admin:orders:toggle:{order.order_number}", user_id),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Ожидание",
                callback_data=codec.encode("admin:orders:set_status:pending", user_id),
            ),
            InlineKeyboardButton(
                text="Оплачен",
                callback_data=codec.encode("admin:orders:set_status:paid", user_id),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="В пути",
                callback_data=codec.encode("admin:orders:set_status:in_transit", user_id),
            ),
            InlineKeyboardButton(
                text="ПВЗ",
                callback_data=codec.encode("admin:orders:set_status:pickup_point", user_id),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Выдан",
                callback_data=codec.encode("admin:orders:set_status:issued", user_id),
            ),
            InlineKeyboardButton(
                text="Отменен",
                callback_data=codec.encode("admin:orders:set_status:cancelled", user_id),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Очистить выбор",
                callback_data=codec.encode("admin:orders:clear", user_id),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Редактировать выбранный",
                callback_data=codec.encode("admin:orders:edit", user_id),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Массово: Цена",
                callback_data=codec.encode("admin:orders:bulk_field:price_rub", user_id),
            ),
            InlineKeyboardButton(
                text="Массово: Комментарий",
                callback_data=codec.encode("admin:orders:bulk_field:manager_comment", user_id),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Массово: Трек",
                callback_data=codec.encode("admin:orders:bulk_field:track_number", user_id),
            ),
            InlineKeyboardButton(
                text="Массово: Детали",
                callback_data=codec.encode("admin:orders:bulk_field:quantity_text", user_id),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️",
                callback_data=codec.encode(f"admin:orders:page:{max(1, page - 1)}", user_id),
            ),
            InlineKeyboardButton(
                text="➡️",
                callback_data=codec.encode(f"admin:orders:page:{min(total_pages, page + 1)}", user_id),
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _get_admin_orders_state(session) -> dict:
    block = session.state_data.get("_admin_orders")
    if isinstance(block, dict):
        page = int(block.get("page", 1))
        selected = block.get("selected", [])
        edit_order = block.get("edit_order")
        edit_field = block.get("edit_field")
        if isinstance(selected, list):
            return {
                "page": page,
                "selected": [str(item) for item in selected],
                "edit_order": str(edit_order) if edit_order else None,
                "edit_field": str(edit_field) if edit_field else None,
                "bulk_field": str(block.get("bulk_field")) if block.get("bulk_field") else None,
                "pending_field": str(block.get("pending_field")) if block.get("pending_field") else None,
                "pending_value": str(block.get("pending_value")) if block.get("pending_value") else None,
            }
    return {
        "page": 1,
        "selected": [],
        "edit_order": None,
        "edit_field": None,
        "bulk_field": None,
        "pending_field": None,
        "pending_value": None,
    }


async def _save_admin_orders_state(container: AppContainer, session, state: dict) -> None:
    payload = dict(session.state_data)
    payload["_admin_orders"] = {
        "page": int(state.get("page", 1)),
        "selected": list(state.get("selected", [])),
        "edit_order": state.get("edit_order"),
        "edit_field": state.get("edit_field"),
        "bulk_field": state.get("bulk_field"),
        "pending_field": state.get("pending_field"),
        "pending_value": state.get("pending_value"),
    }
    session.state_data = payload
    await container.session_repo.save(session)


async def _dispatch_broadcast_text(
    message: Message,
    container: AppContainer,
    backup_service: BackupService,
    audience: str,
    text: str,
) -> tuple[int, int, int]:
    profiles = await backup_service.pick_profiles_for_broadcast(audience)
    tg_sent = 0
    tg_failed = 0
    vk_enqueued = 0
    for profile in profiles:
        if profile.telegram_user_id:
            try:
                await message.bot.send_message(chat_id=profile.telegram_user_id, text=text, parse_mode=None)
                tg_sent += 1
            except Exception as exc:
                tg_failed += 1
                await _mark_blocked_bot_if_needed(container, profile, exc)
        if profile.vk_user_id:
            await container.outbound_repo.enqueue(
                OutboundMessage(
                    id=0,
                    platform=Platform.VK,
                    platform_user_id=int(profile.vk_user_id),
                    message_type="broadcast_text",
                    payload={"text": text},
                )
            )
            vk_enqueued += 1
    return tg_sent, tg_failed, vk_enqueued


async def _dispatch_broadcast_media(
    message: Message,
    container: AppContainer,
    backup_service: BackupService,
    audience: str,
) -> tuple[int, int, int]:
    profiles = await backup_service.pick_profiles_for_broadcast(audience)
    caption = message.caption or ""
    media_kind = ""
    media_id = ""
    if message.photo:
        media_kind = "photo"
        media_id = message.photo[-1].file_id
    elif message.video:
        media_kind = "video"
        media_id = message.video.file_id
    elif message.animation:
        media_kind = "animation"
        media_id = message.animation.file_id
    elif message.document:
        media_kind = "document"
        media_id = message.document.file_id
    tg_sent = 0
    tg_failed = 0
    vk_enqueued = 0
    for profile in profiles:
        if profile.telegram_user_id:
            try:
                if media_kind == "photo":
                    await message.bot.send_photo(
                        chat_id=profile.telegram_user_id,
                        photo=media_id,
                        caption=caption,
                        parse_mode=None,
                    )
                elif media_kind == "video":
                    await message.bot.send_video(
                        chat_id=profile.telegram_user_id,
                        video=media_id,
                        caption=caption,
                        parse_mode=None,
                    )
                elif media_kind == "animation":
                    await message.bot.send_animation(
                        chat_id=profile.telegram_user_id,
                        animation=media_id,
                        caption=caption,
                        parse_mode=None,
                    )
                elif media_kind == "document":
                    await message.bot.send_document(
                        chat_id=profile.telegram_user_id,
                        document=media_id,
                        caption=caption,
                        parse_mode=None,
                    )
                tg_sent += 1
            except Exception as exc:
                tg_failed += 1
                await _mark_blocked_bot_if_needed(container, profile, exc)
        if profile.vk_user_id:
            fallback_text = (
                "Админ отправил медиа-рассылку.\n"
                "В текущей версии для VK доставляется текстовый вариант.\n"
                + (f"\n{caption}" if caption else "")
            )
            await container.outbound_repo.enqueue(
                OutboundMessage(
                    id=0,
                    platform=Platform.VK,
                    platform_user_id=int(profile.vk_user_id),
                    message_type="plain_text",
                    payload={"text": fallback_text},
                )
            )
            vk_enqueued += 1
    return tg_sent, tg_failed, vk_enqueued


async def _notify_order_status_change(
    trigger_message: Message,
    container: AppContainer,
    payment_store: PaymentTextStore,
    codec: CallbackCodec,
    order,
    new_status: OrderStatus,
    note: str = "",
) -> None:
    profile = await container.profile_repo.get_by_id(order.user_profile_id)
    if not profile:
        return
    text = (
        f"Обновление по заказу <b>№{_h(order.order_number)}</b>.\n"
        f"Новый статус: <b>{_h(_order_status_name(new_status))}</b>."
    )
    if note.strip():
        text += f"\nКомментарий: {_h(note.strip())}"
    payment_media_items: list[dict] = []
    if new_status == OrderStatus.WAITING_PAYMENT:
        payment_text = await payment_store.get_text()
        text += "\n\n" + payment_text
        payment_media_items = await payment_store.get_media_items()
    if profile.telegram_user_id:
        try:
            reply_markup = None
            if new_status == OrderStatus.WAITING_PAYMENT:
                reply_markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Оплачено",
                                callback_data=codec.encode(
                                    f"orderpay:paid:{order.order_number}",
                                    int(profile.telegram_user_id),
                                ),
                            ),
                            InlineKeyboardButton(
                                text="❌ Отмена",
                                callback_data=codec.encode(
                                    f"orderpay:cancel:{order.order_number}",
                                    int(profile.telegram_user_id),
                                ),
                            ),
                        ]
                    ]
                )
            await trigger_message.bot.send_message(
                chat_id=profile.telegram_user_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            for payment_media in payment_media_items:
                await send_stored_media_to_telegram(
                    trigger_message.bot,
                    int(profile.telegram_user_id),
                    payment_media,
                )
        except Exception as exc:
            await _mark_blocked_bot_if_needed(container, profile, exc)
    if profile.vk_user_id:
        await container.outbound_repo.enqueue(
            OutboundMessage(
                id=0,
                platform=Platform.VK,
                platform_user_id=int(profile.vk_user_id),
                message_type="plain_text",
                payload={"text": text},
            )
        )


async def _send_order_edit_panel(
    message: Message,
    container: AppContainer,
    user_id: int,
    codec: CallbackCodec,
    state: dict,
    edit: bool = False,
) -> None:
    order_number = str(state.get("edit_order") or "")
    order = await container.order_admin_service.get_order(order_number) if order_number else None
    if not order:
        text = "Заказ не найден. Вернитесь к списку."
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К списку", callback_data=codec.encode("admin:orders:back_list", user_id))]
            ]
        )
        if edit:
            await message.edit_text(text, reply_markup=keyboard)
        else:
            await message.answer(text, reply_markup=keyboard)
        return

    pending_field = state.get("pending_field")
    pending_value = state.get("pending_value")
    text = (
        f"<b>Редактирование заказа {_h(order_number)}</b>\n"
        f"Статус: <b>{_h(_order_status_name(order.status))}</b>\n"
        f"Ссылка: <code>{_h(order.product_url)}</code>\n"
        f"Детали: <code>{_h(order.quantity_text)}</code>\n"
        f"Цена: <code>{_h(order.price_rub if order.price_rub is not None else '—')}</code>\n"
        f"Комментарий: <code>{_h(order.manager_comment or '—')}</code>\n"
        f"Трек: <code>{_h(order.track_number or '—')}</code>\n\n"
        "Выберите поле и отправьте новое значение следующим сообщением."
    )
    if pending_field and pending_value:
        text += (
            "\n\n"
            f"Ожидает подтверждения: <b>{_h(_field_title(str(pending_field)))}</b> = "
            f"<code>{_h(pending_value)}</code>"
        )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Ссылка", callback_data=codec.encode("admin:orders:edit_field:product_url", user_id))],
            [InlineKeyboardButton(text="Количество/размер/цвет", callback_data=codec.encode("admin:orders:edit_field:quantity_text", user_id))],
            [InlineKeyboardButton(text="Цена", callback_data=codec.encode("admin:orders:edit_field:price_rub", user_id))],
            [InlineKeyboardButton(text="Комментарий", callback_data=codec.encode("admin:orders:edit_field:manager_comment", user_id))],
            [InlineKeyboardButton(text="Трек", callback_data=codec.encode("admin:orders:edit_field:track_number", user_id))],
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить изменение",
                    callback_data=codec.encode("admin:orders:confirm_apply", user_id),
                ),
                InlineKeyboardButton(
                    text="❌ Сбросить изменение",
                    callback_data=codec.encode("admin:orders:confirm_cancel", user_id),
                ),
            ],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data=codec.encode("admin:orders:back_list", user_id))],
        ]
    )
    if edit:
        await message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


def _parse_order_status(raw: str) -> OrderStatus | None:
    key = raw.strip().lower()
    aliases = {
        "pending": OrderStatus.PENDING,
        "price_ready": OrderStatus.PRICE_READY,
        "waiting_payment": OrderStatus.WAITING_PAYMENT,
        "paid_check": OrderStatus.PAID_CHECK,
        "paid": OrderStatus.PAID,
        "in_transit": OrderStatus.IN_TRANSIT,
        "pickup_point": OrderStatus.PICKUP_POINT,
        "issued": OrderStatus.ISSUED,
        "cancelled": OrderStatus.CANCELLED,
        "ожидание": OrderStatus.PENDING,
        "цена": OrderStatus.PRICE_READY,
        "оплата": OrderStatus.WAITING_PAYMENT,
        "проверка": OrderStatus.PAID_CHECK,
        "оплачен": OrderStatus.PAID,
        "впути": OrderStatus.IN_TRANSIT,
        "пвз": OrderStatus.PICKUP_POINT,
        "pickup_point": OrderStatus.PICKUP_POINT,
        "выдан": OrderStatus.ISSUED,
        "issued": OrderStatus.ISSUED,
        "отменен": OrderStatus.CANCELLED,
        "cancelled": OrderStatus.CANCELLED,
        "in_transit": OrderStatus.IN_TRANSIT,
    }
    return aliases.get(key)


def _order_status_name(status: OrderStatus) -> str:
    names = {
        OrderStatus.PENDING: "Ожидание",
        OrderStatus.PRICE_READY: "Цена готова",
        OrderStatus.WAITING_PAYMENT: "Ожидает оплату",
        OrderStatus.PAID_CHECK: "Проверка оплаты",
        OrderStatus.PAID: "Оплачен",
        OrderStatus.IN_TRANSIT: "В пути",
        OrderStatus.PICKUP_POINT: "В пункте выдачи",
        OrderStatus.ISSUED: "Выдан",
        OrderStatus.CANCELLED: "Отменен",
    }
    return names.get(status, status.value)


def _field_title(field_name: str) -> str:
    titles = {
        "product_url": "Ссылка",
        "quantity_text": "Количество/размер/цвет",
        "price_rub": "Цена",
        "manager_comment": "Комментарий",
        "track_number": "Трек",
    }
    return titles.get(field_name, field_name)


def _order_field_value(order, field_name: str) -> str:
    if order is None:
        return "—"
    mapping = {
        "product_url": order.product_url,
        "quantity_text": order.quantity_text,
        "price_rub": str(order.price_rub) if order.price_rub is not None else "",
        "manager_comment": order.manager_comment or "",
        "track_number": order.track_number or "",
    }
    return str(mapping.get(field_name, ""))


def _validate_field_input(field_name: str, raw_value: str) -> tuple[bool, str]:
    value = raw_value.strip()
    if field_name == "product_url":
        if not value.startswith(("http://", "https://")):
            return False, "Ссылка должна начинаться с http:// или https://"
        return True, value
    if field_name == "quantity_text":
        if not value:
            return False, "Поле деталей не может быть пустым."
        if len(value) > 250:
            return False, "Слишком длинное значение для деталей (максимум 250 символов)."
        return True, value
    if field_name == "price_rub":
        normalized = value.replace(" ", "")
        if not normalized:
            return True, ""
        if not normalized.isdigit():
            return False, "Цена должна содержать только цифры."
        amount = int(normalized)
        if amount < 0 or amount > 1_000_000_000:
            return False, "Цена вне допустимого диапазона."
        return True, str(amount)
    if field_name == "manager_comment":
        if len(value) > 2000:
            return False, "Комментарий слишком длинный (максимум 2000 символов)."
        return True, value
    if field_name == "track_number":
        if len(value) > 128:
            return False, "Трек слишком длинный (максимум 128 символов)."
        return True, value
    return False, "Неизвестное поле для редактирования."


def _broadcast_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Всем",
                    callback_data=codec.encode("admin:broadcast:all", user_id),
                ),
                InlineKeyboardButton(
                    text="Активные",
                    callback_data=codec.encode("admin:broadcast:active", user_id),
                ),
                InlineKeyboardButton(
                    text="Не активные",
                    callback_data=codec.encode("admin:broadcast:inactive", user_id),
                ),
            ]
        ]
    )


def _utils_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Бэкапы"), KeyboardButton(text="Коды")],
            [KeyboardButton(text="Группа"), KeyboardButton(text="Оплата")],
            [KeyboardButton(text="Оплаты группа")],
            [KeyboardButton(text="Назад")],
        ],
        resize_keyboard=True,
    )


def _payment_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Ред. оплата текст"), KeyboardButton(text="Ред. оплата медиа")],
            [KeyboardButton(text="Очистить медиа оплаты")],
            [KeyboardButton(text="Утилиты"), KeyboardButton(text="Назад")],
        ],
        resize_keyboard=True,
    )


def _group_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Задать группу")],
            [KeyboardButton(text="Создать темы"), KeyboardButton(text="Создать VK логи")],
            [KeyboardButton(text="Сбросить группу")],
            [KeyboardButton(text="Уведомления")],
            [KeyboardButton(text="Утилиты"), KeyboardButton(text="Назад")],
        ],
        resize_keyboard=True,
    )


def _codes_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Коды добавить"), KeyboardButton(text="Коды удалить")],
            [KeyboardButton(text="Утилиты"), KeyboardButton(text="Назад")],
        ],
        resize_keyboard=True,
    )


def _admins_access_keyboard(
    user_id: int,
    codec: CallbackCodec,
    open_for_all: bool,
    is_main: bool,
    admin_ids: list[int],
    main_admin_id: int,
) -> InlineKeyboardMarkup | None:
    if not is_main:
        return None
    text = "🟢 Доступ всем админам" if open_for_all else "🔴 Доступ только главному"
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=text,
                callback_data=codec.encode("admin:admins:toggle_access", user_id),
            )
        ],
        [
            InlineKeyboardButton(
                text="➕ Добавить по ID",
                callback_data=codec.encode("admin:admins:add_id", user_id),
            ),
            InlineKeyboardButton(
                text="➕ Добавить по коду",
                callback_data=codec.encode("admin:admins:add_code", user_id),
            ),
        ],
    ]
    for admin_id in admin_ids:
        if admin_id == main_admin_id:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"❌ Снять {admin_id}",
                    callback_data=codec.encode(f"admin:admins:remove:{admin_id}", user_id),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _payment_group_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Задать оплаты группу")],
            [KeyboardButton(text="Сбросить оплаты группу")],
            [KeyboardButton(text="Утилиты"), KeyboardButton(text="Назад")],
        ],
        resize_keyboard=True,
    )


def _prohibited_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Ред. запрещенка текст"), KeyboardButton(text="Ред. запрещенка медиа")],
            [KeyboardButton(text="Очистить медиа запрещенка")],
            [KeyboardButton(text="Админ"), KeyboardButton(text="Назад")],
        ],
        resize_keyboard=True,
    )


def _content_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Доставка контент"), KeyboardButton(text="Контакты контент")],
            [KeyboardButton(text="Запрещенка")],
            [KeyboardButton(text="Админ"), KeyboardButton(text="Назад")],
        ],
        resize_keyboard=True,
    )


def _faq_admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="FAQ Добавить"), KeyboardButton(text="FAQ Ред. заголовок")],
            [KeyboardButton(text="FAQ Ред. текст"), KeyboardButton(text="FAQ Показать root")],
            [KeyboardButton(text="FAQ Медиа"), KeyboardButton(text="FAQ Очистить медиа")],
            [KeyboardButton(text="Админ"), KeyboardButton(text="Назад")],
        ],
        resize_keyboard=True,
    )


def _orders_root_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Выкупы"), KeyboardButton(text="Самовыкуп")],
            [KeyboardButton(text="Админ"), KeyboardButton(text="Назад")],
        ],
        resize_keyboard=True,
    )


def _delivery_content_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Ред. доставка текст"), KeyboardButton(text="Ред. доставка медиа")],
            [KeyboardButton(text="Очистить медиа доставка")],
            [KeyboardButton(text="Контент"), KeyboardButton(text="Назад")],
        ],
        resize_keyboard=True,
    )


def _contacts_content_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Ред. контакты текст"), KeyboardButton(text="Ред. контакты медиа")],
            [KeyboardButton(text="Очистить медиа контакты")],
            [KeyboardButton(text="Контент"), KeyboardButton(text="Назад")],
        ],
        resize_keyboard=True,
    )


def _notification_settings_text(settings: dict[str, bool]) -> str:
    quiet = "🟢 ВКЛ" if settings.get("quiet_mode") else "🔴 ВЫКЛ"
    bot = "🟢" if settings.get("bot_messages") else "🔴"
    button = "🟢" if settings.get("button_messages") else "🔴"
    user = "🟢" if settings.get("user_messages") else "🔴"
    return (
        "Тихий режим уведомлений для тех-групп.\n"
        f"Тихий режим: {quiet}\n\n"
        f"{bot} От бота\n"
        f"{button} Кнопки бота\n"
        f"{user} Сообщения пользователей\n\n"
        "Нажмите кнопку, чтобы переключить."
    )


def _notifications_keyboard(
    user_id: int,
    codec: CallbackCodec,
    settings: dict[str, bool],
) -> InlineKeyboardMarkup:
    quiet_text = ("🟢 Тихий режим" if settings.get("quiet_mode") else "🔴 Тихий режим")
    bot_text = ("🟢 От бота" if settings.get("bot_messages") else "🔴 От бота")
    btn_text = ("🟢 Кнопки бота" if settings.get("button_messages") else "🔴 Кнопки бота")
    user_text = ("🟢 Сообщения" if settings.get("user_messages") else "🔴 Сообщения")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=quiet_text,
                    callback_data=codec.encode("admin:notify:toggle:quiet_mode", user_id),
                )
            ],
            [
                InlineKeyboardButton(
                    text=bot_text,
                    callback_data=codec.encode("admin:notify:toggle:bot_messages", user_id),
                ),
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=codec.encode("admin:notify:toggle:button_messages", user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=user_text,
                    callback_data=codec.encode("admin:notify:toggle:user_messages", user_id),
                )
            ],
        ]
    )


def _backup_keyboard(user_id: int, codec: CallbackCodec, enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Бэкап БД",
                    callback_data=codec.encode("admin:backup:db", user_id),
                ),
                InlineKeyboardButton(
                    text="Excel",
                    callback_data=codec.encode("admin:backup:excel", user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=("🟢 Авто 4ч: ВКЛ" if enabled else "🔴 Авто 4ч: ВЫКЛ"),
                    callback_data=codec.encode(
                        "admin:backup:auto:off" if enabled else "admin:backup:auto:on",
                        user_id,
                    ),
                )
            ],
        ]
    )


def _get_admin_broadcast_state(session) -> dict:
    block = session.state_data.get("_admin_broadcast")
    if isinstance(block, dict):
        return {
            "awaiting_payload": bool(block.get("awaiting_payload")),
            "audience": str(block.get("audience")) if block.get("audience") else None,
        }
    return {"awaiting_payload": False, "audience": None}


async def _save_admin_broadcast_state(container: AppContainer, session, state: dict) -> None:
    payload = dict(session.state_data)
    payload["_admin_broadcast"] = {
        "awaiting_payload": bool(state.get("awaiting_payload")),
        "audience": state.get("audience"),
    }
    session.state_data = payload
    await container.session_repo.save(session)


def _get_admin_utils_state(session) -> dict:
    block = session.state_data.get("_admin_utils")
    if isinstance(block, dict):
        return {
            "awaiting_payment_text": bool(block.get("awaiting_payment_text")),
            "awaiting_payment_media": bool(block.get("awaiting_payment_media")),
            "awaiting_backup_target": bool(block.get("awaiting_backup_target")),
            "awaiting_payment_review_target": bool(block.get("awaiting_payment_review_target")),
            "awaiting_prohibited_text": bool(block.get("awaiting_prohibited_text")),
            "awaiting_prohibited_media": bool(block.get("awaiting_prohibited_media")),
            "awaiting_delivery_text": bool(block.get("awaiting_delivery_text")),
            "awaiting_delivery_media": bool(block.get("awaiting_delivery_media")),
            "awaiting_contacts_text": bool(block.get("awaiting_contacts_text")),
            "awaiting_contacts_media": bool(block.get("awaiting_contacts_media")),
            "awaiting_profile_search_query": bool(block.get("awaiting_profile_search_query")),
            "profile_search_mode": str(block.get("profile_search_mode")) if block.get("profile_search_mode") else None,
            "awaiting_block_search_query": bool(block.get("awaiting_block_search_query")),
            "block_search_mode": str(block.get("block_search_mode")) if block.get("block_search_mode") else None,
            "block_operation": str(block.get("block_operation")) if block.get("block_operation") else None,
            "awaiting_codes_add": bool(block.get("awaiting_codes_add")),
            "awaiting_codes_remove": bool(block.get("awaiting_codes_remove")),
            "awaiting_faq_action": str(block.get("awaiting_faq_action")) if block.get("awaiting_faq_action") else None,
            "awaiting_faq_media_section_id": (
                int(block.get("awaiting_faq_media_section_id"))
                if block.get("awaiting_faq_media_section_id")
                else None
            ),
            "awaiting_admin_add_id": bool(block.get("awaiting_admin_add_id")),
            "awaiting_admin_add_code": bool(block.get("awaiting_admin_add_code")),
            "awaiting_block_reason_for_code": (
                str(block.get("awaiting_block_reason_for_code")) if block.get("awaiting_block_reason_for_code") else None
            ),
            "awaiting_profile_comment_code": (
                str(block.get("awaiting_profile_comment_code")) if block.get("awaiting_profile_comment_code") else None
            ),
        }
    return {
        "awaiting_payment_text": False,
        "awaiting_payment_media": False,
        "awaiting_backup_target": False,
        "awaiting_payment_review_target": False,
        "awaiting_prohibited_text": False,
        "awaiting_prohibited_media": False,
        "awaiting_delivery_text": False,
        "awaiting_delivery_media": False,
        "awaiting_contacts_text": False,
        "awaiting_contacts_media": False,
        "awaiting_profile_search_query": False,
        "profile_search_mode": None,
        "awaiting_block_search_query": False,
        "block_search_mode": None,
        "block_operation": None,
        "awaiting_codes_add": False,
        "awaiting_codes_remove": False,
        "awaiting_faq_action": None,
        "awaiting_faq_media_section_id": None,
        "awaiting_admin_add_id": False,
        "awaiting_admin_add_code": False,
        "awaiting_block_reason_for_code": None,
        "awaiting_profile_comment_code": None,
    }


async def _save_admin_utils_state(container: AppContainer, session, state: dict) -> None:
    payload = dict(session.state_data)
    payload["_admin_utils"] = {
        "awaiting_payment_text": bool(state.get("awaiting_payment_text")),
        "awaiting_payment_media": bool(state.get("awaiting_payment_media")),
        "awaiting_backup_target": bool(state.get("awaiting_backup_target")),
        "awaiting_payment_review_target": bool(state.get("awaiting_payment_review_target")),
        "awaiting_prohibited_text": bool(state.get("awaiting_prohibited_text")),
        "awaiting_prohibited_media": bool(state.get("awaiting_prohibited_media")),
        "awaiting_delivery_text": bool(state.get("awaiting_delivery_text")),
        "awaiting_delivery_media": bool(state.get("awaiting_delivery_media")),
        "awaiting_contacts_text": bool(state.get("awaiting_contacts_text")),
        "awaiting_contacts_media": bool(state.get("awaiting_contacts_media")),
        "awaiting_profile_search_query": bool(state.get("awaiting_profile_search_query")),
        "profile_search_mode": state.get("profile_search_mode"),
        "awaiting_block_search_query": bool(state.get("awaiting_block_search_query")),
        "block_search_mode": state.get("block_search_mode"),
        "block_operation": state.get("block_operation"),
        "awaiting_codes_add": bool(state.get("awaiting_codes_add")),
        "awaiting_codes_remove": bool(state.get("awaiting_codes_remove")),
        "awaiting_faq_action": state.get("awaiting_faq_action"),
        "awaiting_faq_media_section_id": state.get("awaiting_faq_media_section_id"),
        "awaiting_admin_add_id": bool(state.get("awaiting_admin_add_id")),
        "awaiting_admin_add_code": bool(state.get("awaiting_admin_add_code")),
        "awaiting_block_reason_for_code": state.get("awaiting_block_reason_for_code"),
        "awaiting_profile_comment_code": state.get("awaiting_profile_comment_code"),
    }
    session.state_data = payload
    await container.session_repo.save(session)


def _reset_admin_utils_waiters(state: dict) -> None:
    keys = [
        "awaiting_payment_text",
        "awaiting_payment_media",
        "awaiting_backup_target",
        "awaiting_payment_review_target",
        "awaiting_prohibited_text",
        "awaiting_prohibited_media",
        "awaiting_delivery_text",
        "awaiting_delivery_media",
        "awaiting_contacts_text",
        "awaiting_contacts_media",
        "awaiting_profile_search_query",
        "awaiting_block_search_query",
        "awaiting_codes_add",
        "awaiting_codes_remove",
        "awaiting_admin_add_id",
        "awaiting_admin_add_code",
    ]
    for key in keys:
        state[key] = False
    state["profile_search_mode"] = None
    state["block_search_mode"] = None
    state["block_operation"] = None
    state["awaiting_faq_action"] = None
    state["awaiting_faq_media_section_id"] = None
    state["awaiting_block_reason_for_code"] = None
    state["awaiting_profile_comment_code"] = None


async def _mark_blocked_bot_if_needed(container: AppContainer, profile: UserProfile, error: Exception) -> None:
    # Telegram returns forbidden when user blocked bot or deactivated account.
    if isinstance(error, TelegramForbiddenError):
        if not profile.blocked_bot:
            profile.blocked_bot = True
            await container.profile_repo.save(profile)


_DELETE_MEDIA_RE = re.compile(r"^\s*удалить\s+медиа\s+(\d+)\s*$", re.IGNORECASE)


async def _handle_media_text_command(
    message: Message,
    store: PaymentTextStore | ProhibitedGoodsStore | StaticContentStore | FaqMediaStore,
    section_name: str,
    section_id: int | None = None,
) -> bool:
    text = (message.text or "").strip()
    if text.lower() in {"список медиа", "медиа список"}:
        items = await store.get_media_items(section_id) if section_id is not None else await store.get_media_items()
        await message.answer(
            f"Медиа {section_name}: {len(items)}\n{_media_items_summary(items)}\n"
            "Удаление: «Удалить медиа &lt;номер&gt;»."
        )
        return True
    match = _DELETE_MEDIA_RE.match(text)
    if not match:
        return False
    index = int(match.group(1))
    removed = await store.remove_media_at(section_id, index) if section_id is not None else await store.remove_media_at(index)
    if not removed:
        items = await store.get_media_items(section_id) if section_id is not None else await store.get_media_items()
        await message.answer(
            f"Не удалось удалить медиа #{index}. Сейчас файлов: {len(items)}.\n{_media_items_summary(items)}"
        )
        return True
    items = await store.get_media_items(section_id) if section_id is not None else await store.get_media_items()
    await message.answer(
        f"Медиа #{index} удалено из раздела {section_name}. Осталось: {len(items)}.\n{_media_items_summary(items)}"
    )
    return True


def _media_items_summary(items: list[dict], limit: int = 20) -> str:
    if not items:
        return "Список медиа пуст."
    lines = []
    for idx, item in enumerate(items[:limit], start=1):
        media_type = str(item.get("media_type", "unknown"))
        caption = str(item.get("caption", "")).strip()
        suffix = f" — {_h(caption[:40])}" if caption else ""
        lines.append(f"{idx}. {_h(media_type)}{suffix}")
    if len(items) > limit:
        lines.append(f"... и еще {len(items) - limit}")
    return "\n".join(lines)


async def _archive_media_in_group_topic(
    message: Message,
    group_topics_store: GroupTopicsStore,
    label: str,
) -> tuple[int | None, int | None, int | None]:
    target_chat_id, target_topic_id = await group_topics_store.get_tg_topic("logs")
    if not target_chat_id:
        return None, None, None
    try:
        copied = await message.bot.copy_message(
            chat_id=target_chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=target_topic_id,
        )
    except Exception:
        return None, None, None
    if message.caption:
        try:
            await message.bot.send_message(
                chat_id=target_chat_id,
                text=f"media-archive:{label}",
                message_thread_id=target_topic_id,
                reply_to_message_id=copied.message_id,
            )
        except Exception:
            pass
    return int(target_chat_id), int(target_topic_id) if target_topic_id else None, int(copied.message_id)


async def _sync_vk_attachment_from_tg(
    message: Message,
    container: AppContainer,
    media_type: str,
    file_id: str,
) -> str | None:
    if container.settings.vk is None:
        return None
    media_bytes, filename = await _download_telegram_media(message, file_id)
    if media_bytes is None or not filename:
        return None
    token = container.settings.vk.bot_token
    if media_type == "photo":
        return await _vk_upload_photo(token=token, media_bytes=media_bytes, filename=filename)
    return await _vk_upload_doc(token=token, media_bytes=media_bytes, filename=filename)


async def _download_telegram_media(message: Message, file_id: str) -> tuple[bytes | None, str | None]:
    try:
        tg_file = await message.bot.get_file(file_id)
    except Exception:
        return None, None
    file_path = str(tg_file.file_path or "")
    if not file_path:
        return None, None
    filename = file_path.rsplit("/", maxsplit=1)[-1] or "media.bin"
    url = f"https://api.telegram.org/file/bot{message.bot.token}/{file_path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None, None
                data = await response.read()
    except Exception:
        return None, None
    return data, filename


async def _vk_upload_photo(token: str, media_bytes: bytes, filename: str) -> str | None:
    upload = await _vk_api_call(token, "photos.getMessagesUploadServer", {})
    upload_url = str(upload.get("upload_url", "")).strip() if isinstance(upload, dict) else ""
    if not upload_url:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field("photo", media_bytes, filename=filename, content_type="application/octet-stream")
            async with session.post(upload_url, data=form) as response:
                payload = await response.json(content_type=None)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    save = await _vk_api_call(
        token,
        "photos.saveMessagesPhoto",
        {
            "server": payload.get("server"),
            "photo": payload.get("photo"),
            "hash": payload.get("hash"),
        },
    )
    if not isinstance(save, list) or not save:
        return None
    item = save[0]
    if not isinstance(item, dict):
        return None
    owner_id = item.get("owner_id")
    media_id = item.get("id")
    if owner_id is None or media_id is None:
        return None
    return f"photo{owner_id}_{media_id}"


async def _vk_upload_doc(token: str, media_bytes: bytes, filename: str) -> str | None:
    upload = await _vk_api_call(token, "docs.getMessagesUploadServer", {"type": "doc"})
    upload_url = str(upload.get("upload_url", "")).strip() if isinstance(upload, dict) else ""
    if not upload_url:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field("file", media_bytes, filename=filename, content_type="application/octet-stream")
            async with session.post(upload_url, data=form) as response:
                payload = await response.json(content_type=None)
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload.get("file"):
        return None
    save = await _vk_api_call(token, "docs.save", {"file": payload.get("file"), "title": filename})
    if not isinstance(save, dict):
        return None
    doc = save.get("doc")
    if not isinstance(doc, dict):
        return None
    owner_id = doc.get("owner_id")
    media_id = doc.get("id")
    if owner_id is None or media_id is None:
        return None
    return f"doc{owner_id}_{media_id}"


async def _vk_create_logs_chat(token: str) -> int | None:
    response = await _vk_api_call(token, "messages.createChat", {"title": "Логи"})
    if isinstance(response, int):
        chat_id = response
    elif isinstance(response, dict):
        chat_id = response.get("chat_id") or response.get("id")
    else:
        chat_id = None
    if not chat_id:
        return None
    return 2_000_000_000 + int(chat_id)


async def _vk_api_call(token: str, method: str, params: dict) -> object:
    api_url = f"https://api.vk.com/method/{method}"
    payload = dict(params)
    payload["access_token"] = token
    payload["v"] = "5.199"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, data=payload) as response:
                raw = await response.json(content_type=None)
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw.get("response", {})


def _h(value: object) -> str:
    if value is None:
        return "—"
    return escape(str(value), quote=False)
