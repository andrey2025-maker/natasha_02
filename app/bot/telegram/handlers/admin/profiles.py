from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.telegram.callbacks import CallbackCodec
from app.bot.telegram.callback_panel import edit_panel_message
from app.bot.telegram.handlers.admin.html import _h
from app.core.container import AppContainer
from app.domain.models import UserProfile
from app.services.admin_tools_service import AdminProfileCommentStore, BlockReasonStore

async def _send_profiles_page(
    message: Message,
    user_id: int,
    page: int,
    container: AppContainer,
    codec: CallbackCodec,
    edit: bool = False,
) -> None:
    safe_page = max(1, page)
    items = await container.admin_service.list_profiles(page=safe_page, page_size=9)
    if not items and safe_page > 1:
        safe_page -= 1
        items = await container.admin_service.list_profiles(page=safe_page, page_size=9)
    if not items:
        text = "Профилей пока нет."
        if edit:
            await edit_panel_message(message, text=text)
        else:
            await message.answer(text)
        return
    lines = ["Профили:"]
    for p in items:
        lines.append(_profile_list_item_text(p))
    text = "\n\n".join(lines)
    keyboard = _profiles_pagination(user_id, safe_page, codec, items)
    if edit:
        await edit_panel_message(message, text=text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


def _profiles_pagination(user_id: int, page: int, codec: CallbackCodec, items: list[UserProfile]):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="⬅️",
                callback_data=codec.encode(f"admin:profiles:page:{max(1, page - 1)}", user_id),
            ),
            InlineKeyboardButton(
                text="🔍",
                callback_data=codec.encode("admin:profiles:search_menu", user_id),
            ),
            InlineKeyboardButton(
                text="➡️",
                callback_data=codec.encode(f"admin:profiles:page:{page + 1}", user_id),
            ),
        ]
    ]
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
        return "🔴"
    return "🟢"


def _profile_list_item_text(profile: UserProfile) -> str:
    status = _profile_state_emoji(profile)
    name_value = _h(profile.name or "Нет")
    if profile.telegram_user_id:
        name_value = f"<a href='tg://user?id={int(profile.telegram_user_id)}'>{name_value}</a>"
    vk_value = "Нет"
    if profile.vk_user_id:
        vk_id = int(profile.vk_user_id)
        vk_value = f"<a href='https://vk.com/id{vk_id}'>vk.com/id{vk_id}</a>"
    details = (
        f"{status} 🆔 Код: {_h(profile.code)}\n"
        f"👤 Имя: {name_value}\n"
        f"📞 Тел: {_h(profile.phone or 'Нет')}\n"
        f"🏙 Город: {_h(profile.city or 'Нет')}\n"
        f"💰 Стоимость: {_h(profile.price_per_kg_rub)} RUB за 1 кг\n"
        f"🌍 Загран Паспорт: {'Да' if profile.has_passport else 'Нет'}\n"
        f"💬 Комментарий: —\n"
        f"🆔 ID: {_h(profile.telegram_user_id or 'Нет')}\n"
        f"🔗 ВК: {vk_value}\n"
        f"🕒 Последняя активность: {_h(profile.last_activity_at.strftime('%Y-%m-%d %H:%M'))}\n"
        f"📅 Дата регистрации: {_h(profile.created_at.strftime('%Y-%m-%d %H:%M'))}"
    )
    return f"<blockquote expandable>{details}</blockquote>"


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
    rows = [
        [
            InlineKeyboardButton(
                text="🆔 Код",
                callback_data=codec.encode(f"admin:profile:edit_field:{profile.code}:code", user_id),
            ),
            InlineKeyboardButton(
                text="👤 Имя",
                callback_data=codec.encode(f"admin:profile:edit_field:{profile.code}:name", user_id),
            ),
            InlineKeyboardButton(
                text="📞 Тел",
                callback_data=codec.encode(f"admin:profile:edit_field:{profile.code}:phone", user_id),
            ),
        ],
        [
            InlineKeyboardButton(
                text="🏙 Город",
                callback_data=codec.encode(f"admin:profile:edit_field:{profile.code}:city", user_id),
            ),
            InlineKeyboardButton(
                text="🌍 Загран",
                callback_data=codec.encode(f"admin:profile:edit_field:{profile.code}:passport", user_id),
            ),
            InlineKeyboardButton(
                text="💰 Стоимость",
                callback_data=codec.encode(f"admin:profile:edit_field:{profile.code}:price_per_kg", user_id),
            ),
        ],
        [
            InlineKeyboardButton(
                text="💬 Коммент",
                callback_data=codec.encode(f"admin:profile:edit_field:{profile.code}:comment", user_id),
            ),
        ],
    ]
    rows.extend(list(block_kb.inline_keyboard))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _profile_edit_fields_keyboard(profile_code: str, user_id: int, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Код",
                    callback_data=codec.encode(f"admin:profile:edit_field:{profile_code}:code", user_id),
                ),
                InlineKeyboardButton(
                    text="Имя",
                    callback_data=codec.encode(f"admin:profile:edit_field:{profile_code}:name", user_id),
                ),
                InlineKeyboardButton(
                    text="Телефон",
                    callback_data=codec.encode(f"admin:profile:edit_field:{profile_code}:phone", user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Город",
                    callback_data=codec.encode(f"admin:profile:edit_field:{profile_code}:city", user_id),
                ),
                InlineKeyboardButton(
                    text="Загран паспорт",
                    callback_data=codec.encode(f"admin:profile:edit_field:{profile_code}:passport", user_id),
                ),
                InlineKeyboardButton(
                    text="Стоимость",
                    callback_data=codec.encode(f"admin:profile:edit_field:{profile_code}:price_per_kg", user_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Комментарий",
                    callback_data=codec.encode(f"admin:profile:edit_field:{profile_code}:comment", user_id),
                )
            ],
        ]
    )


def _normalize_profile_code(raw: str) -> str | None:
    stripped = raw.strip()
    if not stripped.isdigit():
        return None
    return stripped.zfill(3)


async def _migrate_profile_code_metadata(
    old_code: str,
    new_code: str,
    *,
    container: AppContainer,
    block_reason_store: BlockReasonStore,
    profile_comment_store: AdminProfileCommentStore,
) -> None:
    if old_code == new_code:
        return
    block_reason = await block_reason_store.get_reason(old_code)
    if block_reason:
        await block_reason_store.set_reason(new_code, block_reason)
        await block_reason_store.clear_reason(old_code)
    comment = await profile_comment_store.get_comment(old_code)
    if comment:
        await profile_comment_store.set_comment(new_code, comment)
        await profile_comment_store.set_comment(old_code, "")
    await container.sync_repo.rename_active_profile_code(old_code, new_code)


def _profile_details(
    profile: UserProfile,
    block_reason: str | None = None,
    profile_comment: str | None = None,
) -> str:
    blocked_admin_text = "Да" if profile.is_blocked_by_admin else "Нет"
    blocked_bot_text = "Да" if profile.blocked_bot else "Нет"
    reason_line = block_reason or "—"
    comment_line = profile_comment or "—"
    name_value = _h(profile.name or "—")
    if profile.telegram_user_id:
        name_value = f"<a href='tg://user?id={int(profile.telegram_user_id)}'>{name_value}</a>"
    vk_value = "Нет"
    if profile.vk_user_id:
        vk_id = int(profile.vk_user_id)
        vk_value = f"<a href='https://vk.com/id{vk_id}'>id{vk_id}</a>"
    details = (
        f"{_profile_state_emoji(profile)} 🆔 Код: {_h(profile.code)}\n"
        f"👤 Имя: {name_value}\n"
        f"📞 Тел: {_h(profile.phone or 'Нет')}\n"
        f"🏙 Город: {_h(profile.city or 'Нет')}\n"
        f"💰 Стоимость: {_h(profile.price_per_kg_rub)} RUB за 1 кг\n"
        f"🌍 Загран Паспорт: {'Да' if profile.has_passport else 'Нет'}\n"
        f"💬 Комментарий: {_h(comment_line)}\n"
        f"🆔 ID: {_h(profile.telegram_user_id or 'Нет')}\n"
        f"🔗 ВК: {vk_value}\n"
        f"🕒 Последняя активность: {_h(profile.last_activity_at.strftime('%Y-%m-%d %H:%M'))}\n"
        f"📅 Дата регистрации: {_h(profile.created_at.strftime('%Y-%m-%d %H:%M'))}\n"
        f"⛔ Заблокирован админом: {_h(blocked_admin_text)}\n"
        f"🧾 Причина блокировки: {_h(reason_line)}\n"
        f"🔴 Отписан от бота: {_h(blocked_bot_text)}"
    )
    return f"<blockquote expandable>{details}</blockquote>"
