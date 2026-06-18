from __future__ import annotations

from html import escape

from app.domain.models import UserProfile


def welcome_text() -> str:
    return (
        "Добро пожаловать в Cargo_Omsk55 🇷🇺🇨🇳📦\n\n"
        "Доставка товаров из Китая в Омск от 200 грамм.\n\n"
        "Срок доставки:\n"
        "🚚 10–15 дней\n"
        "Стоимость:\n"
        "💰 560 ₽ за 1 кг"
    )


def profile_intro() -> str:
    return (
        "👋 <b>Профиль клиента</b>\n\n"
        "Для удобной коммуникации укажите: <b>Имя</b>, <b>Телефон</b>, <b>Город получения</b>.\n"
        "После заполнения вы получите ваш <b>Код для заказов</b>."
    )


def profile_summary(profile: UserProfile) -> str:
    return (
        "🪪 <b>Ваш профиль</b>\n\n"
        f"👤 <b>Имя:</b> {_h(profile.name)}\n"
        f"🔢 <b>Код:</b> {_h(profile.code)}\n"
        f"📞 <b>Тел:</b> {_h(profile.phone)}\n"
        f"🏙 <b>Город:</b> {_h(profile.city)}\n"
        f"🛂 <b>Загран паспорт:</b> {'Да' if profile.has_passport else 'Нет'}"
    )


def ask_name() -> str:
    return "📝 Напишите ваше <b>имя</b>:"


def ask_phone(name: str) -> str:
    return f"✅ Ваше имя: <b>{_h(name)}</b>\n\n📞 Напишите ваш <b>номер телефона</b>:"


def ask_city(name: str, phone: str) -> str:
    return (
        f"👤 Имя: <b>{_h(name)}</b>\n"
        f"📞 Телефон: <b>{_h(phone)}</b>\n\n"
        "🏙 Напишите ваш <b>город</b> получения:"
    )


def confirm_profile(name: str, phone: str, city: str) -> str:
    return (
        "🔎 <b>Проверьте данные</b>\n\n"
        f"👤 Имя: <b>{_h(name)}</b>\n"
        f"📞 Тел: <b>{_h(phone)}</b>\n"
        f"🏙 Город: <b>{_h(city)}</b>\n\n"
        "Всё верно?"
    )


def ask_has_code() -> str:
    return "❓ Вы уже были нашим клиентом и у вас есть код для заказов?"


def enter_existing_code() -> str:
    return "🔢 Напишите ваш код для заказов:"


def confirm_code(code: str) -> str:
    return f"✅ Код <b>{_h(code)}</b> указан правильно?"


def ask_passport(code: str) -> str:
    return (
        f"🔐 Ваш код: <b>{_h(code)}</b>\n\n"
        "🛂 Есть ли у вас загранпаспорт?\n"
        "Он нужен для таможенного оформления посылок."
    )


def platforms_text() -> str:
    return (
        "🛒 <b>Китайские площадки с которыми мы работаем</b>\n\n"
        "📦 <a href='https://www.poizon.com'>Poizon</a> "
        "(<a href='https://apps.apple.com'>IOS</a>/<a href='https://play.google.com'>Android</a>)\n"
        "Маркетплейс брендовой одежды и обуви.\n\n"
        "🛍 <a href='https://www.taobao.com'>Taobao</a> "
        "(<a href='https://apps.apple.com'>IOS</a>/<a href='https://play.google.com'>Android</a>)\n"
        "Крупнейший китайский маркетплейс.\n\n"
        "🏭 <a href='https://www.1688.com'>1688</a> "
        "(<a href='https://apps.apple.com'>IOS</a>/<a href='https://play.google.com'>Android</a>)\n"
        "Оптовые закупки напрямую у производителей.\n\n"
        "🐟 <a href='https://www.goofish.com'>Xianyu (Рыбка)</a> "
        "(<a href='https://apps.apple.com'>IOS</a>/<a href='https://play.google.com'>Android</a>)\n"
        "Б/у товары и редкие позиции.\n\n"
        "🛒 <a href='https://www.pinduoduo.com'>Pinduoduo</a> "
        "(<a href='https://apps.apple.com'>IOS</a>/<a href='https://play.google.com'>Android</a>)\n"
        "Выгодные покупки и акции."
    )


def code_not_found() -> str:
    return "⚠️ Код не найден. Проверьте и введите снова."


def sync_enter_profile_code() -> str:
    return "🔗 Введите ваш код клиента, к которому уже привязан профиль ВК:"


def sync_vk_profile_missing() -> str:
    return "⚠️ Для этого кода профиль ВК не найден.\nСначала зарегистрируйтесь в ВК-боте."


def sync_tg_profile_missing() -> str:
    return "⚠️ Для этого кода профиль ТГ не найден.\nСначала зарегистрируйтесь в Telegram-боте."


def sync_code_sent() -> str:
    return (
        "✅ Запрос на синхронизацию создан.\n"
        "📨 Код подтверждения отправлен в профиль другой платформы.\n"
        "⏳ Введите код здесь в течение 2 минут."
    )


def sync_temporarily_blocked() -> str:
    return "⛔ Слишком много неверных попыток. Повторите позже."


def sync_cooldown() -> str:
    return "⏱ Можно создавать только один запрос синхронизации в минуту."


def sync_request_not_found() -> str:
    return "⚠️ Активный запрос синхронизации не найден. Запустите процесс заново."


def sync_code_invalid() -> str:
    return "❌ Неверный код подтверждения. Проверьте и отправьте еще раз."


def sync_code_expired() -> str:
    return "⌛ Срок действия кода истек. Запустите синхронизацию заново."


def sync_done(profile: UserProfile) -> str:
    return (
        "🎉 Профили успешно синхронизированы.\n\n"
        f"👤 <b>Имя:</b> {_h(profile.name)}\n"
        f"🔢 <b>Код:</b> {_h(profile.code)}\n"
        f"📞 <b>Тел:</b> {_h(profile.phone)}\n"
        f"🏙 <b>Город:</b> {_h(profile.city)}"
    )


def sync_code_for_other_platform(code: str, profile_code: str, from_platform: str) -> str:
    return (
        "🔔 Запрос на привязку профиля.\n"
        f"🧭 Платформа-инициатор: <b>{_h(from_platform.upper())}</b>\n"
        f"🔢 Код клиента: <b>{_h(profile_code)}</b>\n\n"
        f"🔐 Код подтверждения: <b>{_h(code)}</b>\n"
        "Передайте этот код в диалог другой платформы."
    )


def unknown_state() -> str:
    return "⚙️ Не удалось обработать действие. Нажмите /start."


def _h(value: object) -> str:
    if value is None:
        return "—"
    return escape(str(value), quote=False)
