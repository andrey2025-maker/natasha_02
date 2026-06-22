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
from app.bot.telegram.handlers.admin.tracks import admin_tracks_has_pending, try_handle_admin_tracks_text
from app.services.dialog_topic_profile_sync import refresh_dialog_topic_profile


def register_text_catchall(router: Router, ctx: AdminContext) -> None:
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

    @router.message()
    async def admin_order_edit_input(message: Message) -> None:
        if not await _ensure_admin(message):
            raise SkipHandler
        if not message.from_user:
            raise SkipHandler
        if not message.text:
            raise SkipHandler
        session = await container.profile_flow.get_or_create_session(Platform.TELEGRAM, message.from_user.id)
        broadcast_state = _get_admin_broadcast_state(session)
        utils_state = _get_admin_utils_state(session)
        orders_state = _get_admin_orders_state(session)
        text = message.text.strip()

        has_utils = admin_utils_has_waiter(utils_state)
        has_broadcast = bool(broadcast_state.get("awaiting_payload"))
        has_orders_edit = bool(
            orders_state.get("edit_field")
            or orders_state.get("bulk_field")
            or orders_state.get("pending_field")
            or orders_state.get("awaiting_order_search_query")
        )
        has_tracks_input = admin_tracks_has_pending(session)
        has_dialog_fsm = session.state != DialogState.IDLE

        if is_cancel_command(text):
            if not (has_utils or has_broadcast or has_orders_edit or has_tracks_input or has_dialog_fsm):
                raise SkipHandler
            await _clear_admin_input_states(container, session)
            if has_dialog_fsm:
                await container.profile_flow.cancel_to_idle(session)
            is_main = message.from_user.id == container.settings.telegram.main_admin_id
            await message.answer(
                "Ввод отменён.",
                reply_markup=main_menu_keyboard(include_admin=is_main),
            )
            return

        if is_navigation_command(text) and (has_utils or has_broadcast or has_orders_edit or has_tracks_input):
            await _clear_admin_input_states(container, session)
            if text in {"Профиль", "👤 Профиль"}:
                await _open_user_profile_from_admin(message, container, callback_codec)
                return
            await message.answer("Предыдущий ввод отменён. Повторите нажатие кнопки.")
            return

        if not has_utils and not has_broadcast and not has_tracks_input:
            has_orders_input = bool(
                orders_state.get("awaiting_order_search_query")
                or orders_state.get("bulk_field")
                or (
                    orders_state.get("edit_order")
                    and orders_state.get("edit_field")
                )
            )
            if not has_orders_input:
                raise SkipHandler

        if await try_handle_admin_tracks_text(
            message,
            container=container,
            payment_store=payment_store,
            codec=callback_codec,
            session=session,
        ):
            return

        edit_order = orders_state.get("edit_order")
        edit_field = orders_state.get("edit_field")
        bulk_field = orders_state.get("bulk_field")

        if await try_handle_content_utils_text(
            message,
            codec=callback_codec,
            utils_state=utils_state,
            prohibited_store=prohibited_store,
            contacts_store=contacts_store,
            group_topics_store=group_topics_store,
            container=container,
        ):
            await _save_admin_utils_state(container, session, utils_state)
            return

        if utils_state.get("awaiting_payment_text"):
            new_text = message.text.strip()
            if not new_text:
                await message.answer("Текст не может быть пустым.")
                return
            await payment_store.save_text(new_text)
            utils_state["awaiting_payment_text"] = False
            await _save_admin_utils_state(container, session, utils_state)
            if utils_state.get("awaiting_payment_media"):
                await message.answer("Текст оплаты обновлен. Теперь отправьте медиа или нажмите «Готово медиа».")
            else:
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

        if utils_state.get("awaiting_faq_media_section_id"):
            if str(utils_state.get("faq_admin_screen") or "") == SCREEN_EDIT_MEDIA:
                await message.answer(
                    "Сейчас ожидается медиа. Отправьте фото, видео или GIF, "
                    "либо нажмите «Готово медиа» в сообщении выше."
                )
            return

        if await try_handle_faq_admin_text(
            message,
            container=container,
            codec=callback_codec,
            faq_media_store=faq_media_store,
            utils_state=utils_state,
        ):
            await _save_admin_utils_state(container, session, utils_state)
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

        profile_edit_code = str(utils_state.get("awaiting_profile_edit_code") or "").strip()
        profile_edit_field = str(utils_state.get("awaiting_profile_edit_field") or "").strip()
        if profile_edit_code and profile_edit_field:
            profile = await container.profile_repo.get_by_code(profile_edit_code)
            if not profile:
                utils_state["awaiting_profile_edit_code"] = None
                utils_state["awaiting_profile_edit_field"] = None
                await _save_admin_utils_state(container, session, utils_state)
                await message.answer("Профиль не найден.")
                return
            value = message.text.strip()
            if profile_edit_field == "name":
                profile.name = value
                await container.profile_repo.save(profile)
                answer_text = "Имя обновлено."
            elif profile_edit_field == "phone":
                profile.phone = value
                await container.profile_repo.save(profile)
                answer_text = "Телефон обновлен."
            elif profile_edit_field == "city":
                profile.city = value
                await container.profile_repo.save(profile)
                answer_text = "Город обновлен."
            elif profile_edit_field == "passport":
                normalized = value.lower()
                if normalized in {"да", "yes", "y", "1"}:
                    profile.has_passport = True
                elif normalized in {"нет", "no", "n", "0"}:
                    profile.has_passport = False
                else:
                    await message.answer("Введите «Да» или «Нет».")
                    return
                await container.profile_repo.save(profile)
                answer_text = "Поле «Загран паспорт» обновлено."
            elif profile_edit_field == "comment":
                if value == "-":
                    await profile_comment_store.set_comment(profile_edit_code, "")
                    answer_text = "Комментарий очищен."
                else:
                    await profile_comment_store.set_comment(profile_edit_code, value)
                    answer_text = "Комментарий обновлен."
            elif profile_edit_field == "code":
                new_code = _normalize_profile_code(value)
                if not new_code:
                    await message.answer("Код должен содержать только цифры. Пример: 016")
                    return
                if new_code == profile.code:
                    await message.answer("Новый код совпадает с текущим.")
                    return
                if await container.profile_repo.is_code_taken(new_code):
                    await message.answer(f"Код <b>{_h(new_code)}</b> уже занят другим профилем.", parse_mode="HTML")
                    return
                if await container.profile_repo.is_code_reserved(new_code):
                    await message.answer(f"Код <b>{_h(new_code)}</b> находится в резерве.", parse_mode="HTML")
                    return
                old_code = profile.code
                await _migrate_profile_code_metadata(
                    old_code,
                    new_code,
                    container=container,
                    block_reason_store=block_reason_store,
                    profile_comment_store=profile_comment_store,
                )
                profile.code = new_code
                await container.profile_repo.save(profile)
                answer_text = f"Код обновлён: {old_code} → {new_code}."
            else:
                await message.answer("Неизвестное поле.")
                return
            utils_state["awaiting_profile_edit_code"] = None
            utils_state["awaiting_profile_edit_field"] = None
            await _save_admin_utils_state(container, session, utils_state)
            block_reason = await block_reason_store.get_reason(profile.code)
            profile_comment = await profile_comment_store.get_comment(profile.code)
            await message.answer(answer_text)
            _refresh_group_topic_profile(message.bot, container=container, profile=profile)
            await message.answer(
                _profile_details(profile, block_reason=block_reason, profile_comment=profile_comment),
                parse_mode="HTML",
                reply_markup=_profile_actions_keyboard(profile, message.from_user.id, callback_codec),
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
            profile = await container.profile_repo.get_by_code(profile_comment_code)
            await message.answer("Комментарий профиля обновлен.")
            if profile:
                _refresh_group_topic_profile(message.bot, container=container, profile=profile)
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
            profile = await container.profile_repo.get_by_code(block_reason_code)
            await message.answer("Пользователь заблокирован.")
            if profile:
                _refresh_group_topic_profile(message.bot, container=container, profile=profile)
            return

        if utils_state.get("awaiting_backup_target"):
            parts = message.text.split()
            if len(parts) != 1:
                await message.answer(fsm_prompt("Формат: chat_id, например: -1001234567890"))
                return
            try:
                chat_id = int(parts[0])
            except ValueError:
                await message.answer("chat_id должен быть числом.")
                return
            await group_topics_store.set_tg_chat_id(chat_id)
            topics = await _create_required_group_topics(
                bot=message.bot,
                chat_id=chat_id,
                group_topics_store=group_topics_store,
                backup_service=backup_service,
                payment_target_store=payment_target_store,
            )
            if topics is None:
                await message.answer(
                    "Не удалось создать темы. Проверьте, что группа с chat_id — форум, "
                    "а у бота есть права управления темами."
                )
                return
            logs_topic_id, payment_topic_id, questions_topic_id, buyout_topic_id = topics
            created_count, existed_count, failed_count = await _provision_topics_for_existing_telegram_profiles(
                bot=message.bot,
                chat_id=chat_id,
                container=container,
                group_topics_store=group_topics_store,
                topic_dialog_store=topic_dialog_store,
            )
            utils_state["awaiting_backup_target"] = False
            await _save_admin_utils_state(container, session, utils_state)
            await message.answer(
                "Группа обновлена, темы созданы и подключены:\n"
                f"- chat_id: {chat_id}\n"
                f"- логи: {logs_topic_id}\n"
                f"- оплата: {payment_topic_id}\n"
                f"- вопросы: {questions_topic_id}\n"
                f"- Выкупы: {buyout_topic_id}\n\n"
                "Диалоги пользователей:\n"
                f"- создано тем: {created_count}\n"
                f"- уже существовали: {existed_count}\n"
                f"- ошибок создания: {failed_count}"
            )
            return

        if utils_state.get("awaiting_payment_review_target"):
            parts = message.text.split()
            if len(parts) not in {1, 2}:
                await message.answer(fsm_prompt("Формат: chat_id [topic_id], например: -1001234567890 42"))
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

        if state.get("awaiting_order_search_query"):
            mode = str(state.get("order_search_mode") or "").strip().lower()
            query = (message.text or "").strip()
            if not query:
                await message.answer("Введите непустой запрос.")
                return
            results = await container.order_admin_service.search_orders(by=mode, query=query, limit=90)
            state["awaiting_order_search_query"] = False
            state["order_search_mode"] = None
            state["search_results"] = [order.order_number for order in results]
            state["page"] = 1
            await _save_admin_orders_state(container, session, state)
            if not results:
                await message.answer("Заказы не найдены.")
            await _send_orders_panel(
                message,
                container,
                callback_codec,
                message.from_user.id,
                state,
                session,
            )
            return

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
