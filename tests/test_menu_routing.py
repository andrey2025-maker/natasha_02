from __future__ import annotations

from app.bot.telegram.menu_texts import (
    ADMIN_MENU_TEXTS,
    BUYOUT_DIALOG_STATES_TEXTS,
    DELEGATED_MENU_TEXTS,
    MENU_TEXTS_SKIP_IDLE_FORWARD,
    USER_FACING_MENU_TEXTS,
)
from app.bot.telegram.fsm_utils import NAVIGATION_BUTTONS, MAIN_MENU_BUTTONS


def test_menu_texts_cover_admin_utils() -> None:
  assert ADMIN_MENU_TEXTS <= MENU_TEXTS_SKIP_IDLE_FORWARD
  assert "Готово медиа" in MENU_TEXTS_SKIP_IDLE_FORWARD
  assert "Бэкапы" in MENU_TEXTS_SKIP_IDLE_FORWARD


def test_user_facing_excludes_admin_only_buttons() -> None:
  assert "Профили" not in USER_FACING_MENU_TEXTS
  assert "👤 Профиль" in USER_FACING_MENU_TEXTS
  assert "🛠 Админ" not in USER_FACING_MENU_TEXTS


def test_navigation_includes_admin_menu() -> None:
  assert ADMIN_MENU_TEXTS <= NAVIGATION_BUTTONS
  assert BUYOUT_DIALOG_STATES_TEXTS <= NAVIGATION_BUTTONS


def test_main_menu_buttons_are_routed() -> None:
  assert MAIN_MENU_BUTTONS <= DELEGATED_MENU_TEXTS | BUYOUT_DIALOG_STATES_TEXTS | {"Назад"}
