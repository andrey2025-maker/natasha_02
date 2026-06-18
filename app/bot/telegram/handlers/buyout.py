from __future__ import annotations

from html import escape
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.telegram.callbacks import CallbackAuthError, CallbackCodec
from app.bot.telegram.keyboards.profile import my_orders_filters_keyboard, my_orders_pagination_keyboard
from app.core.container import AppContainer
from app.domain.enums import DialogState, OrderStatus, Platform
from app.domain.models import OutboundMessage
from app.services.admin_tools_service import (
    BuyoutQuoteDraftStore,
    GroupTopicsStore,
    NotificationSettingsStore,
    PaymentTextStore,
    PaymentReviewTargetStore,
    send_stored_media_to_telegram,
)
from app.services.flows.buyout_flow import BuyoutFlowResponse


def build_buyout_router(container: AppContainer) -> Router:
    router = Router()
    platform = Platform.TELEGRAM
    callback_codec = CallbackCodec(container.callback_signer)
    payment_target_store = PaymentReviewTargetStore(container.settings.database.dsn)
    notification_settings_store = NotificationSettingsStore(container.settings.database.dsn)
    group_topics_store = GroupTopicsStore(container.settings.database.dsn)
    quote_draft_store = BuyoutQuoteDraftStore(container.settings.database.dsn)
    payment_store = PaymentTextStore(container.settings.database.dsn)

    async def _is_blocked_user(user_id: int) -> bool:
        profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, user_id)
        return bool(profile and profile.is_blocked_by_admin)

    async def _reply(message: Message, response: BuyoutFlowResponse) -> None:
        kwargs = {"parse_mode": "HTML"}
        if response.reply_markup is not None:
            kwargs["reply_markup"] = response.reply_markup
        await message.answer(response.text, **kwargs)

    @router.message(F.text.in_({"Заказ выкупа", "🛍 Заказ выкупа"}))
    async def start_buyout(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        response = await container.buyout_flow.start(session)
        await _reply(message, response)

    @router.message(F.text.in_({"Мои заказы", "📦 Мои заказы"}))
    async def show_my_orders(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        response = await container.buyout_flow.render_orders(session, page=1)
        if response.state_data:
            response.reply_markup = my_orders_pagination_keyboard(
                user_id=message.from_user.id,
                current_page=int(response.state_data.get("page", 1)),
                total_pages=int(response.state_data.get("total_pages", 1)),
                codec=callback_codec,
            )
        await _reply(message, response)

    @router.message(F.text.in_({"Фильтры заказов", "🎛 Фильтры заказов"}))
    async def show_filters(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        await container.buyout_flow.prepare_preferences(session)
        filters = container.buyout_flow.filter_states(session)
        await message.answer(
            container.buyout_flow.filters_hint_text(session),
            parse_mode="HTML",
            reply_markup=my_orders_filters_keyboard(
                user_id=message.from_user.id,
                filters=filters,
                codec=callback_codec,
            ),
        )

    @router.callback_query()
    async def my_orders_pagination(callback: CallbackQuery) -> None:
        if not callback.data or not callback.from_user or not callback.message:
            return
        if await _is_blocked_user(callback.from_user.id):
            await callback.answer("Доступ ограничен", show_alert=True)
            return
        try:
            action = callback_codec.decode(callback.data, callback.from_user.id)
        except CallbackAuthError:
            return
        if not action.startswith(
            (
                "payreview:",
                "orderpay:",
                "orderquote:",
                "buydraft:",
                "orders_filter:",
                "my_orders:",
            )
        ):
            raise SkipHandler
        if action.startswith("buydraft:"):
            if not await container.admin_service.is_admin(callback.from_user.id):
                await callback.answer("Только для админов", show_alert=True)
                return
            parts = action.split(":", maxsplit=2)
            if len(parts) != 3:
                await callback.answer("Некорректная команда", show_alert=True)
                return
            draft_action = parts[1]
            order_number = parts[2]
            order = await container.order_admin_service.get_order(order_number)
            if not order:
                await callback.answer("Заказ не найден", show_alert=True)
                return
            draft = await quote_draft_store.get(order_number)
            if not draft:
                await callback.answer("Черновик цены не найден", show_alert=True)
                return
            if draft_action == "send":
                price_rub = int(draft.get("price_rub") or 0)
                manager_comment = str(draft.get("manager_comment") or "").strip()
                profile = await container.profile_repo.get_by_id(order.user_profile_id)
                if not profile or not profile.telegram_user_id:
                    await callback.answer("Пользователь недоступен", show_alert=True)
                    return
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=OrderStatus.PRICE_READY,
                    changed_by_user_id=callback.from_user.id,
                    note="quote sent to user",
                    platform=Platform.TELEGRAM,
                )
                if not updated:
                    await callback.answer("Не удалось обновить", show_alert=True)
                    return
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Подтвердить",
                                callback_data=callback_codec.encode(
                                    f"orderquote:confirm:{order_number}",
                                    int(profile.telegram_user_id),
                                ),
                            ),
                            InlineKeyboardButton(
                                text="❌ Отменить",
                                callback_data=callback_codec.encode(
                                    f"orderquote:cancel:{order_number}",
                                    int(profile.telegram_user_id),
                                ),
                            ),
                        ]
                    ]
                )
                text = (
                    f"По заказу <b>№{_h(order_number)}</b> готова цена.\n"
                    f"Стоимость: <b>{price_rub} ₽</b>."
                )
                if manager_comment:
                    text += f"\nКомментарий: {_h(manager_comment)}"
                text += "\n\nПодтвердите заказ или отмените."
                try:
                    await callback.bot.send_message(
                        chat_id=int(profile.telegram_user_id),
                        text=text,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                except Exception:
                    await callback.answer("Не удалось отправить клиенту", show_alert=True)
                    return
                await callback.answer("Отправлено клиенту")
                await callback.message.edit_text(
                    (callback.message.text or "") + "\n\n📤 Отправлено клиенту",
                    parse_mode="HTML",
                    reply_markup=None,
                )
                await quote_draft_store.clear(order_number)
                return
            if draft_action == "cancel":
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=OrderStatus.CANCELLED,
                    changed_by_user_id=callback.from_user.id,
                    note="quote cancelled by manager",
                    platform=Platform.TELEGRAM,
                )
                if not updated:
                    await callback.answer("Не удалось обновить", show_alert=True)
                    return
                await callback.answer("Заявка отменена")
                await callback.message.edit_text(
                    (callback.message.text or "") + "\n\n❌ Отменено менеджером",
                    parse_mode="HTML",
                    reply_markup=None,
                )
                await quote_draft_store.clear(order_number)
                await _notify_user_status_changed_by_bot(
                    bot=callback.bot,
                    container=container,
                    order=updated,
                    status=OrderStatus.CANCELLED,
                    note="Заявка отменена менеджером до этапа оплаты.",
                )
                return
            await callback.answer("Неизвестное действие", show_alert=True)
            return
        if action.startswith("orderquote:"):
            parts = action.split(":", maxsplit=2)
            if len(parts) != 3:
                await callback.answer("Некорректная команда", show_alert=True)
                return
            quote_action = parts[1]
            order_number = parts[2]
            profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, callback.from_user.id)
            if not profile:
                await callback.answer("Профиль не найден", show_alert=True)
                return
            order = await container.order_admin_service.get_order(order_number)
            if not order or order.user_profile_id != profile.id:
                await callback.answer("Заказ не найден", show_alert=True)
                return
            if quote_action == "confirm":
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=OrderStatus.WAITING_PAYMENT,
                    changed_by_user_id=callback.from_user.id,
                    note="user confirmed quote",
                    platform=Platform.TELEGRAM,
                )
                if not updated:
                    await callback.answer("Не удалось обновить", show_alert=True)
                    return
                pay_keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="💳 Оплатить",
                                callback_data=callback_codec.encode(f"orderquote:pay:{order_number}", callback.from_user.id),
                            )
                        ]
                    ]
                )
                await callback.answer("Подтверждено")
                await callback.message.edit_text(
                    f"Вы подтвердили цену по заказу <b>{_h(order_number)}</b>.",
                    parse_mode="HTML",
                    reply_markup=None,
                )
                await callback.message.answer(
                    "Нажмите кнопку «Оплатить», чтобы получить реквизиты и продолжить.",
                    reply_markup=pay_keyboard,
                )
                return
            if quote_action == "cancel":
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=OrderStatus.CANCELLED,
                    changed_by_user_id=callback.from_user.id,
                    note="user cancelled after quote",
                    platform=Platform.TELEGRAM,
                )
                if not updated:
                    await callback.answer("Не удалось обновить", show_alert=True)
                    return
                await callback.answer("Заказ отменен")
                await callback.message.edit_text(
                    f"Заказ <b>{_h(order_number)}</b> отменен.",
                    parse_mode="HTML",
                    reply_markup=None,
                )
                return
            if quote_action == "pay":
                instruction = await payment_store.get_text()
                payment_media_items = await payment_store.get_media_items()
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Оплачено",
                                callback_data=callback_codec.encode(f"orderpay:paid:{order_number}", callback.from_user.id),
                            ),
                            InlineKeyboardButton(
                                text="❌ Отмена оплаты",
                                callback_data=callback_codec.encode(f"orderpay:cancel:{order_number}", callback.from_user.id),
                            ),
                        ]
                    ]
                )
                await callback.answer()
                await callback.message.answer(instruction, parse_mode="HTML", reply_markup=keyboard)
                for payment_media in payment_media_items:
                    await send_stored_media_to_telegram(callback.bot, callback.from_user.id, payment_media)
                return
            await callback.answer("Неизвестное действие", show_alert=True)
            return
        if action.startswith("payreview:"):
            if not await container.admin_service.is_admin(callback.from_user.id):
                await callback.answer("Только для админов", show_alert=True)
                return
            parts = action.split(":", maxsplit=2)
            if len(parts) != 3:
                await callback.answer("Некорректная команда", show_alert=True)
                return
            review_action = parts[1]
            order_number = parts[2]
            order = await container.order_admin_service.get_order(order_number)
            if not order:
                await callback.answer("Заказ не найден", show_alert=True)
                return
            if review_action == "approve":
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=OrderStatus.PAID,
                    changed_by_user_id=callback.from_user.id,
                    note="payment approved by admin",
                    platform=Platform.TELEGRAM,
                )
                if not updated:
                    await callback.answer("Не удалось обновить", show_alert=True)
                    return
                await _notify_user_status_changed(
                    callback,
                    container,
                    order=updated,
                    status=OrderStatus.PAID,
                    note="Оплата подтверждена администратором.",
                )
                await callback.answer("Оплата подтверждена")
                await callback.message.edit_text(
                    callback.message.text + "\n\n✅ Подтверждено",
                    parse_mode="HTML",
                    reply_markup=None,
                )
                await _notify_payment_group_event(
                    callback,
                    container,
                    payment_target_store=payment_target_store,
                    group_topics_store=group_topics_store,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                    event_text=f"✅ Подтверждено админом {callback.from_user.id} ({_omsk_now_text()})",
                )
                return
            if review_action == "reject":
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=OrderStatus.CANCELLED,
                    changed_by_user_id=callback.from_user.id,
                    note="payment rejected by admin",
                    platform=Platform.TELEGRAM,
                )
                if not updated:
                    await callback.answer("Не удалось обновить", show_alert=True)
                    return
                await _notify_user_status_changed(
                    callback,
                    container,
                    order=updated,
                    status=OrderStatus.CANCELLED,
                    note="Оплата отклонена администратором.",
                )
                await callback.answer("Оплата отклонена")
                await callback.message.edit_text(
                    callback.message.text + "\n\n❌ Отклонено",
                    parse_mode="HTML",
                    reply_markup=None,
                )
                await _notify_payment_group_event(
                    callback,
                    container,
                    payment_target_store=payment_target_store,
                    group_topics_store=group_topics_store,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                    event_text=f"❌ Отклонено админом {callback.from_user.id} ({_omsk_now_text()})",
                )
                return
            await callback.answer("Неизвестное действие", show_alert=True)
            return

        if action.startswith("orderpay:"):
            parts = action.split(":", maxsplit=2)
            if len(parts) != 3:
                await callback.answer("Некорректная команда", show_alert=True)
                return
            pay_action = parts[1]
            order_number = parts[2]
            profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, callback.from_user.id)
            if not profile:
                await callback.answer("Профиль не найден", show_alert=True)
                return
            order = await container.order_admin_service.get_order(order_number)
            if not order or order.user_profile_id != profile.id:
                await callback.answer("Заказ не найден", show_alert=True)
                return
            if pay_action == "paid":
                new_status = OrderStatus.PAID_CHECK
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=new_status,
                    changed_by_user_id=callback.from_user.id,
                    note="user marked paid",
                    platform=Platform.TELEGRAM,
                )
                if not updated:
                    await callback.answer("Не удалось обновить", show_alert=True)
                    return
                await callback.answer("Платеж отправлен на проверку")
                await callback.message.edit_reply_markup(reply_markup=None)
                await callback.message.answer(
                    f"Заявка <b>{order_number}</b> помечена как «Проверка оплаты».",
                    parse_mode="HTML",
                )
                await _notify_admin_payment_event(
                    callback,
                    container,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                    event_text="Клиент нажал «Оплачено»",
                )
                await _notify_payment_group_event(
                    callback,
                    container,
                    payment_target_store=payment_target_store,
                    group_topics_store=group_topics_store,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                    event_text=f"Клиент {callback.from_user.id} нажал «Оплачено» ({_omsk_now_text()})",
                )
                await _send_payment_review_to_admins(
                    callback,
                    container,
                    codec=callback_codec,
                    payment_target_store=payment_target_store,
                    group_topics_store=group_topics_store,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                )
                return
            if pay_action == "cancel":
                new_status = OrderStatus.CANCELLED
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=new_status,
                    changed_by_user_id=callback.from_user.id,
                    note="user cancelled payment",
                    platform=Platform.TELEGRAM,
                )
                if not updated:
                    await callback.answer("Не удалось обновить", show_alert=True)
                    return
                await callback.answer("Заказ отменен")
                await callback.message.edit_reply_markup(reply_markup=None)
                await callback.message.answer(
                    f"Заявка <b>{order_number}</b> отменена.",
                    parse_mode="HTML",
                )
                await _notify_admin_payment_event(
                    callback,
                    container,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                    event_text="Клиент нажал «Отмена оплаты»",
                )
                await _notify_payment_group_event(
                    callback,
                    container,
                    payment_target_store=payment_target_store,
                    group_topics_store=group_topics_store,
                    notification_settings_store=notification_settings_store,
                    order_number=order_number,
                    event_text=f"Клиент {callback.from_user.id} нажал «Отмена оплаты» ({_omsk_now_text()})",
                )
                return
            await callback.answer("Неизвестное действие", show_alert=True)
            return
        if action.startswith("orders_filter:"):
            session = await container.profile_flow.get_or_create_session(platform, callback.from_user.id)
            raw = action.split(":", maxsplit=1)[1]
            if raw == "reset":
                await container.buyout_flow.reset_status_filters(session)
            else:
                try:
                    status = OrderStatus(raw)
                except ValueError:
                    await callback.answer("Неизвестный фильтр", show_alert=True)
                    return
                await container.buyout_flow.toggle_status_filter(session, status)
            filters = container.buyout_flow.filter_states(session)
            await callback.answer("Фильтр обновлен")
            await callback.message.edit_text(
                container.buyout_flow.filters_hint_text(session),
                parse_mode="HTML",
                reply_markup=my_orders_filters_keyboard(
                    user_id=callback.from_user.id,
                    filters=filters,
                    codec=callback_codec,
                ),
            )
            return

        if not action.startswith("my_orders:"):
            return
        try:
            page = int(action.split(":", maxsplit=1)[1])
        except ValueError:
            await callback.answer()
            return
        session = await container.profile_flow.get_or_create_session(platform, callback.from_user.id)
        response = await container.buyout_flow.render_orders(session, page=page)
        if response.state_data:
            response.reply_markup = my_orders_pagination_keyboard(
                user_id=callback.from_user.id,
                current_page=int(response.state_data.get("page", 1)),
                total_pages=int(response.state_data.get("total_pages", 1)),
                codec=callback_codec,
            )
        await callback.answer()
        await callback.message.edit_text(response.text, parse_mode="HTML", reply_markup=response.reply_markup)

    @router.message(F.photo | F.video | F.animation | F.document)
    async def handle_buyout_media(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        if session.state != DialogState.BUYOUT_WAIT_MEDIA:
            raise SkipHandler
        user_key = f"tg:{message.from_user.id}"
        if not container.rate_limiter.allow_request(user_key, "<media>"):
            return
        media_size_bytes = (
            (message.photo[-1].file_size if message.photo else None)
            or (message.video.file_size if message.video else None)
            or (message.animation.file_size if message.animation else None)
            or (message.document.file_size if message.document else None)
        )
        media_size_mb = int(media_size_bytes / (1024 * 1024)) if media_size_bytes else None
        if not container.rate_limiter.validate_user_payload_size(text_size=0, media_size_mb=media_size_mb):
            await message.answer("Файл слишком большой. Максимум 20 МБ.")
            return
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
        archive_chat_id, archive_topic_id, archive_message_id = await _archive_buyout_media_in_group(
            message=message,
            group_topics_store=group_topics_store,
        )
        media_group_id = message.media_group_id
        response = await container.buyout_flow.handle_media(
            session,
            media_group_id,
            storage_chat_id=archive_chat_id,
            storage_topic_id=archive_topic_id,
            storage_message_id=archive_message_id,
            media_type=media_type or "unknown",
            tg_file_id=file_id or None,
        )
        await _reply(message, response)

    @router.message(F.chat.type.in_({"group", "supergroup"}), F.reply_to_message, F.text)
    async def handle_group_price_reply(message: Message) -> None:
        if not message.from_user or not message.text or not message.reply_to_message:
            return
        if not await container.admin_service.is_admin(message.from_user.id):
            return

        target_chat_id, target_topic_id = await payment_target_store.get_target()
        if not target_chat_id:
            target_chat_id, target_topic_id = await group_topics_store.get_tg_topic("payment")
        if not target_chat_id or int(message.chat.id) != int(target_chat_id):
            return
        if target_topic_id and message.message_thread_id != int(target_topic_id):
            return

        source_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        order_number = _extract_order_number_from_text(source_text)
        if not order_number:
            return
        parsed = _parse_group_price_input(message.text)
        if not parsed:
            await message.reply(
                "Формат ответа: `2000` или `2000 | комментарий`.",
                parse_mode="Markdown",
            )
            return
        price_rub, comment = parsed

        order = await container.order_admin_service.get_order(order_number)
        if not order:
            await message.reply("Заказ не найден.")
            return
        try:
            await container.order_admin_service.update_order_field(order_number, "price_rub", str(price_rub))
            if comment:
                await container.order_admin_service.update_order_field(order_number, "manager_comment", comment)
        except Exception:
            await message.reply("Не удалось сохранить цену.")
            return
        updated = await container.order_admin_service.set_status(
            order_number=order_number,
            new_status=OrderStatus.PRICE_READY,
            changed_by_user_id=message.from_user.id,
            note=f"group price set: {price_rub}" + (f" | {comment}" if comment else ""),
            platform=Platform.TELEGRAM,
        )
        if not updated:
            await message.reply("Не удалось обновить статус.")
            return

        await quote_draft_store.save(
            order_number=order_number,
            price_rub=price_rub,
            manager_comment=comment,
            manager_user_id=message.from_user.id,
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📤 Отправить клиенту",
                        callback_data=callback_codec.encode(f"buydraft:send:{order_number}", message.from_user.id),
                    ),
                    InlineKeyboardButton(
                        text="❌ Отменить заявку",
                        callback_data=callback_codec.encode(f"buydraft:cancel:{order_number}", message.from_user.id),
                    ),
                ]
            ]
        )
        preview_text = f"🧾 Черновик цены для <b>{_h(order_number)}</b>\nЦена: <b>{price_rub} ₽</b>"
        if comment:
            preview_text += f"\nКомментарий: {_h(comment)}"
        preview_text += "\n\nПроверьте и отправьте клиенту."
        await message.reply(preview_text, parse_mode="HTML", reply_markup=keyboard)

    @router.message()
    async def buyout_text_flow(message: Message) -> None:
        if not message.from_user or not message.text:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        if message.text.startswith("/"):
            raise SkipHandler
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        if session.state not in {
            DialogState.BUYOUT_WAIT_LINK,
            DialogState.BUYOUT_WAIT_DETAILS,
            DialogState.BUYOUT_ADD_MORE,
        }:
            raise SkipHandler
        user_key = f"tg:{message.from_user.id}"
        if not container.rate_limiter.allow_request(user_key, message.text):
            return
        if not container.rate_limiter.validate_user_payload_size(len(message.text)):
            await message.answer("Сообщение слишком длинное.")
            return
        previous_state = session.state
        response = await container.buyout_flow.handle_text(session, message.text)
        await _reply(message, response)
        if previous_state == DialogState.BUYOUT_WAIT_DETAILS and response.state == DialogState.BUYOUT_ADD_MORE:
            await _notify_new_buyout_order(
                message=message,
                container=container,
                payment_target_store=payment_target_store,
                group_topics_store=group_topics_store,
                notification_settings_store=notification_settings_store,
            )

    return router


async def _notify_admin_payment_event(
    callback: CallbackQuery,
    container: AppContainer,
    notification_settings_store: NotificationSettingsStore,
    order_number: str,
    event_text: str,
) -> None:
    if not callback.from_user:
        return
    try:
        silent = await notification_settings_store.should_disable_notification("button")
        await callback.bot.send_message(
            chat_id=container.settings.telegram.main_admin_id,
            text=(
                "Событие оплаты:\n"
                f"Заказ: <b>{_h(order_number)}</b>\n"
                f"Пользователь TG ID: <code>{callback.from_user.id}</code>\n"
                f"Действие: {_h(event_text)}"
            ),
            parse_mode="HTML",
            disable_notification=silent,
        )
    except Exception:
        return


async def _send_payment_review_to_admins(
    callback: CallbackQuery,
    container: AppContainer,
    codec: CallbackCodec,
    payment_target_store: PaymentReviewTargetStore,
    group_topics_store: GroupTopicsStore,
    notification_settings_store: NotificationSettingsStore,
    order_number: str,
) -> None:
    if not callback.from_user:
        return
    admin_ids = await container.admin_service.list_admins()
    text = (
        "Проверка оплаты:\n"
        f"Заказ: <b>{_h(order_number)}</b>\n"
        f"Клиент TG ID: <code>{callback.from_user.id}</code>\n"
        "Выберите действие:"
    )
    for admin_id in admin_ids:
        if not admin_id:
            continue
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Подтвердить",
                        callback_data=codec.encode(f"payreview:approve:{order_number}", admin_id),
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=codec.encode(f"payreview:reject:{order_number}", admin_id),
                    ),
                ]
            ]
        )
        try:
            silent = await notification_settings_store.should_disable_notification("button")
            await callback.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_notification=silent,
            )
        except Exception:
            continue
    target_chat_id, target_topic_id = await group_topics_store.get_tg_topic("buyout")
    if not target_chat_id:
        target_chat_id, target_topic_id = await payment_target_store.get_target()
    if not target_chat_id:
        target_chat_id, target_topic_id = await group_topics_store.get_tg_topic("payment")
    if target_chat_id:
        try:
            silent = await notification_settings_store.should_disable_notification("button")
            await callback.bot.send_message(
                chat_id=target_chat_id,
                text=text + "\n\nРешение по кнопкам доступно в личных сообщениях админов.",
                parse_mode="HTML",
                disable_notification=silent,
                message_thread_id=target_topic_id,
            )
        except Exception:
            pass


async def _notify_payment_group_event(
    callback: CallbackQuery,
    container: AppContainer,
    payment_target_store: PaymentReviewTargetStore,
    group_topics_store: GroupTopicsStore,
    notification_settings_store: NotificationSettingsStore,
    order_number: str,
    event_text: str,
) -> None:
    target_chat_id, target_topic_id = await payment_target_store.get_target()
    if not target_chat_id:
        target_chat_id, target_topic_id = await group_topics_store.get_tg_topic("payment")
    if not target_chat_id:
        return
    try:
        silent = await notification_settings_store.should_disable_notification("button")
        await callback.bot.send_message(
            chat_id=target_chat_id,
            text=(
                "Событие оплаты:\n"
                f"Заказ: <b>{_h(order_number)}</b>\n"
                f"{_h(event_text)}"
            ),
            parse_mode="HTML",
            disable_notification=silent,
            message_thread_id=target_topic_id,
        )
    except Exception:
        return


async def _notify_user_status_changed(
    callback: CallbackQuery,
    container: AppContainer,
    order,
    status: OrderStatus,
    note: str,
) -> None:
    await _notify_user_status_changed_by_bot(
        bot=callback.bot,
        container=container,
        order=order,
        status=status,
        note=note,
    )


async def _notify_user_status_changed_by_bot(
    bot,
    container: AppContainer,
    order,
    status: OrderStatus,
    note: str,
) -> None:
    profile = await container.profile_repo.get_by_id(order.user_profile_id)
    if not profile:
        return
    text = (
        f"Обновление по заказу <b>№{_h(order.order_number)}</b>.\n"
        f"Новый статус: <b>{_h(_status_title(status))}</b>\n"
        f"{_h(note)}"
    )
    if profile.telegram_user_id:
        try:
            await bot.send_message(
                chat_id=profile.telegram_user_id,
                text=text,
                parse_mode="HTML",
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


async def _notify_new_buyout_order(
    message: Message,
    container: AppContainer,
    payment_target_store: PaymentReviewTargetStore,
    group_topics_store: GroupTopicsStore,
    notification_settings_store: NotificationSettingsStore,
) -> None:
    if not message.from_user:
        return
    profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, message.from_user.id)
    if not profile:
        return
    orders = await container.buyout_repo.list_for_user(profile.id, limit=1, offset=0)
    if not orders:
        return
    order = orders[0]
    target_chat_id, target_topic_id = await payment_target_store.get_target()
    if not target_chat_id:
        target_chat_id, target_topic_id = await group_topics_store.get_tg_topic("payment")
    if not target_chat_id:
        return
    text = (
        f"🆕 <b>Новый выкуп №{_h(order.order_number)}</b>\n"
        "Статус: <b>Ожидание</b>\n"
        f"Профиль: <b>{_h(profile.code)}</b> ({_h(profile.name or 'без имени')})\n"
        f"TG ID: <code>{message.from_user.id}</code>\n"
        f"Ссылка: {_h(order.product_url)}\n"
        f"Детали: {_h(order.quantity_text)}\n\n"
        "Ответьте на это сообщение:\n"
        "<code>2000</code> или <code>2000 | комментарий</code>"
    )
    try:
        disable_notification = await notification_settings_store.should_disable_notification("user")
        sent = await message.bot.send_message(
            chat_id=target_chat_id,
            text=text,
            parse_mode="HTML",
            message_thread_id=target_topic_id,
            disable_notification=disable_notification,
        )
        if order.media_storage_chat_id and order.media_storage_message_id:
            try:
                await message.bot.copy_message(
                    chat_id=target_chat_id,
                    from_chat_id=int(order.media_storage_chat_id),
                    message_id=int(order.media_storage_message_id),
                    message_thread_id=target_topic_id,
                    reply_to_message_id=sent.message_id,
                )
            except Exception:
                pass
    except Exception:
        return


def _status_title(status: OrderStatus) -> str:
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


def _omsk_now_text() -> str:
    return datetime.now(ZoneInfo("Asia/Omsk")).strftime("%d.%m.%Y %H:%M")


async def _mark_blocked_bot_if_needed(container: AppContainer, profile, error: Exception) -> None:
    if isinstance(error, TelegramForbiddenError):
        if not profile.blocked_bot:
            profile.blocked_bot = True
            await container.profile_repo.save(profile)


async def _archive_buyout_media_in_group(
    message: Message,
    group_topics_store: GroupTopicsStore,
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
    return int(target_chat_id), int(target_topic_id) if target_topic_id else None, int(copied.message_id)


def _extract_order_number_from_text(text: str) -> str | None:
    match = re.search(r"Выкуп\s*№\s*([A-Za-z0-9/_-]+)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def _parse_group_price_input(text: str) -> tuple[int, str] | None:
    raw = text.strip()
    if not raw:
        return None
    if "|" in raw:
        price_part, comment_part = raw.split("|", maxsplit=1)
        comment = comment_part.strip()
    else:
        price_part = raw
        comment = ""
    digits = "".join(ch for ch in price_part if ch.isdigit())
    if not digits:
        return None
    price = int(digits)
    if price <= 0:
        return None
    return price, comment


def _h(value: object) -> str:
    if value is None:
        return "—"
    return escape(str(value), quote=False)
