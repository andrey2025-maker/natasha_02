from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.bot.telegram.callbacks import CallbackCodec

UTILS_PANEL_TEXT = (
    "🛠 Утилиты админки\n"
    "Тонкая настройка и управление\n"
    "Выберите инструмент:"
)


def _utils_inline_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👥 Группы", callback_data=codec.encode("admin:utils:group", user_id)),
                InlineKeyboardButton(text="🤝 Рефералы", callback_data=codec.encode("admin:utils:ref", user_id)),
            ],
            [
                InlineKeyboardButton(text="💾 Бэкапы", callback_data=codec.encode("admin:utils:backups", user_id)),
                InlineKeyboardButton(text="👑 Админы", callback_data=codec.encode("admin:utils:admins", user_id)),
            ],
            [
                InlineKeyboardButton(text="🔑 Коды", callback_data=codec.encode("admin:utils:codes", user_id)),
                InlineKeyboardButton(text="💳 Оплата", callback_data=codec.encode("admin:utils:payment", user_id)),
            ],
            [
                InlineKeyboardButton(text="📇 Контакты", callback_data=codec.encode("admin:utils:contacts", user_id)),
                InlineKeyboardButton(text="🔞 Запрещёнка", callback_data=codec.encode("admin:utils:prohibited", user_id)),
            ],
        ]
    )


def _utils_back_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=codec.encode("admin:utils:root", user_id))]
        ]
    )


def _utils_group_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Добавить", callback_data=codec.encode("admin:utils:group:add", user_id)),
                InlineKeyboardButton(
                    text="Уведомления",
                    callback_data=codec.encode("admin:utils:group:notifications", user_id),
                ),
            ],
            [InlineKeyboardButton(text="Темы", callback_data=codec.encode("admin:utils:group:topics", user_id))],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=codec.encode("admin:utils:root", user_id))],
        ]
    )


def _utils_topics_keyboard(user_id: int, codec: CallbackCodec, selected: list[str]) -> InlineKeyboardMarkup:
    def _btn(label: str, key: str) -> InlineKeyboardButton:
        mark = "🟢" if key in selected else "🔴"
        return InlineKeyboardButton(
            text=f"{mark} {label}",
            callback_data=codec.encode(f"admin:utils:group:topics:toggle:{key}", user_id),
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("Код", "code"), _btn("Имя", "name")],
            [_btn("Телефон", "phone"), _btn("Город", "city")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=codec.encode("admin:utils:group", user_id))],
        ]
    )


def _codes_inline_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Добавить", callback_data=codec.encode("admin:utils:codes:add", user_id)),
                InlineKeyboardButton(text="Удалить", callback_data=codec.encode("admin:utils:codes:remove", user_id)),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=codec.encode("admin:utils:root", user_id))],
        ]
    )


def _payment_inline_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Ред.", callback_data=codec.encode("admin:utils:payment:edit", user_id)),
                InlineKeyboardButton(
                    text="Готово медиа",
                    callback_data=codec.encode("admin:utils:payment:media_done", user_id),
                ),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=codec.encode("admin:utils:root", user_id))],
        ]
    )


def _admin_root_inline_keyboard(user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Профили",
                    callback_data=codec.encode("admin:menu:profiles", user_id),
                ),
                InlineKeyboardButton(
                    text="📦 Заказы",
                    callback_data=codec.encode("admin:menu:orders", user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 Статистика",
                    callback_data=codec.encode("admin:menu:stats", user_id),
                ),
                InlineKeyboardButton(
                    text="📢 Рассылка",
                    callback_data=codec.encode("admin:menu:broadcast", user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Блокировки",
                    callback_data=codec.encode("admin:menu:blocks", user_id),
                ),
                InlineKeyboardButton(
                    text="❓ Вопросы",
                    callback_data=codec.encode("admin:menu:faq", user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔞 Админы",
                    callback_data=codec.encode("admin:menu:admins", user_id),
                ),
                InlineKeyboardButton(
                    text="🛠 Утилиты",
                    callback_data=codec.encode("admin:menu:utils", user_id),
                ),
            ],
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


def _admins_access_keyboard_with_back(
    user_id: int,
    codec: CallbackCodec,
    open_for_all: bool,
    is_main: bool,
    admin_ids: list[int],
    main_admin_id: int,
) -> InlineKeyboardMarkup:
    base = _admins_access_keyboard(
        user_id=user_id,
        codec=codec,
        open_for_all=open_for_all,
        is_main=is_main,
        admin_ids=admin_ids,
        main_admin_id=main_admin_id,
    )
    rows = list(base.inline_keyboard) if base else []
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=codec.encode("admin:utils:root", user_id))])
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
            [KeyboardButton(text="Доставка контент")],
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


def _notifications_keyboard_with_back(
    user_id: int,
    codec: CallbackCodec,
    settings: dict[str, bool],
) -> InlineKeyboardMarkup:
    base = _notifications_keyboard(user_id, codec, settings)
    rows = list(base.inline_keyboard)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=codec.encode("admin:utils:group", user_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def _backup_keyboard_with_back(user_id: int, codec: CallbackCodec, enabled: bool) -> InlineKeyboardMarkup:
    base = _backup_keyboard(user_id, codec, enabled)
    rows = list(base.inline_keyboard)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=codec.encode("admin:utils:root", user_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)
