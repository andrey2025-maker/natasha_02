from __future__ import annotations

from app.bot.telegram.menu_texts import ADMIN_MENU_TEXTS, BUYOUT_DIALOG_STATES_TEXTS, DELEGATED_MENU_TEXTS

FSM_CANCEL_HINT = "\n\nДля отмены отправьте /отмена"

MAIN_MENU_BUTTONS = frozenset(
    {
        "Профиль",
        "👤 Профиль",
        "Вопросы",
        "❓ Вопросы",
        "Запрещенные товары",
        "🚫 Запрещенные товары",
        "Как работает доставка",
        "🚚 Как работает доставка",
        "Наши контакты",
        "☎️ Наши контакты",
        "Админ",
        "🛠 Админ",
        "🛠️ Админ",
        "Заказ выкупа",
        "🛍 Заказ выкупа",
        "Мои заказы",
        "📦 Мои заказы",
        "Фильтры заказов",
        "🎛 Фильтры заказов",
        "Заполнить профиль",
        "Ещё товар",
        "Нет",
        "Назад",
    }
)

NAVIGATION_BUTTONS = DELEGATED_MENU_TEXTS | BUYOUT_DIALOG_STATES_TEXTS | ADMIN_MENU_TEXTS


def fsm_prompt(text: str) -> str:
    body = text.rstrip()
    if FSM_CANCEL_HINT.strip() in body:
        return body
    return f"{body}{FSM_CANCEL_HINT}"


def is_cancel_command(text: str | None) -> bool:
    if not text:
        return False
    normalized = text.strip().lower()
    return normalized in {"/отмена", "/cancel", "отмена"}


def is_navigation_command(text: str | None) -> bool:
    if not text:
        return False
    return text.strip() in NAVIGATION_BUTTONS


def admin_utils_has_waiter(utils_state: dict) -> bool:
    waiter_flags = (
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
    )
    if any(utils_state.get(flag) for flag in waiter_flags):
        return True
    if utils_state.get("awaiting_faq_media_section_id"):
        return True
    if utils_state.get("faq_admin_screen") in {"add_title", "rename_title", "edit_text"}:
        return True
    if utils_state.get("content_utils_screen") == "edit_text":
        return True
    if utils_state.get("awaiting_content_utils_media"):
        return True
    if utils_state.get("awaiting_block_reason_for_code"):
        return True
    if utils_state.get("awaiting_profile_comment_code"):
        return True
    if utils_state.get("awaiting_profile_edit_code"):
        return True
    return False
