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
from app.bot.telegram.keyboards.profile import (
    buyout_add_more_inline_keyboard,
    main_menu_keyboard,
    my_orders_message_keyboard,
)
from app.core.container import AppContainer
from app.domain.enums import DialogState, OrderStatus, Platform
from app.domain.models import OutboundMessage
from app.services.admin_tools_service import (
    BuyoutQuoteDraftStore,
    GroupTopicsStore,
    NotificationSettingsStore,
    PaymentRejectPendingStore,
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
    payment_reject_pending_store = PaymentRejectPendingStore(container.settings.database.dsn)
    payment_store = PaymentTextStore(container.settings.database.dsn)

    async def _is_blocked_user(user_id: int) -> bool:
        profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, user_id)
        return bool(profile and profile.is_blocked_by_admin)

    async def _reply(message: Message, response: BuyoutFlowResponse) -> None:
        kwargs = {"parse_mode": "HTML"}
        if response.reply_markup is not None:
            kwargs["reply_markup"] = response.reply_markup
        elif response.state == DialogState.BUYOUT_ADD_MORE and message.from_user:
            kwargs["reply_markup"] = buyout_add_more_inline_keyboard(
                user_id=message.from_user.id,
                codec=callback_codec,
            )
        elif response.state == DialogState.IDLE:
            is_admin = bool(message.from_user and await container.admin_service.is_admin(message.from_user.id))
            kwargs["reply_markup"] = main_menu_keyboard(include_admin=is_admin)
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

    async def _orders_reply_markup(
        user_id: int,
        session,
        response: BuyoutFlowResponse,
    ) -> InlineKeyboardMarkup | None:
        if not response.state_data:
            return None
        filters = container.buyout_flow.filter_states(session)
        return my_orders_message_keyboard(
            user_id=user_id,
            current_page=int(response.state_data.get("page", 1)),
            total_pages=int(response.state_data.get("total_pages", 1)),
            filters=filters,
            codec=callback_codec,
        )

    @router.message(F.text.in_({"Мои заказы", "📦 Мои заказы"}))
    async def show_my_orders(message: Message) -> None:
        if not message.from_user:
            return
        if await _is_blocked_user(message.from_user.id):
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return
        session = await container.profile_flow.get_or_create_session(platform, message.from_user.id)
        await container.buyout_flow.prepare_preferences(session)
        response = await container.buyout_flow.render_orders(session, page=1)
        response.reply_markup = await _orders_reply_markup(message.from_user.id, session, response)
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
        response = await container.buyout_flow.render_orders(session, page=1)
        response.reply_markup = await _orders_reply_markup(message.from_user.id, session, response)
        await _reply(message, response)

    @router.callback_query()
    async def my_orders_pagination(callback: CallbackQuery) -> None:
        if not callback.data or not callback.from_user or not callback.message:
            return
        if await _is_blocked_user(callback.from_user.id):
            await callback.answer("Доступ ограничен", show_alert=True)
            return
        try:
            action = callback_codec.decode_public(callback.data)
        except CallbackAuthError:
            try:
                action = callback_codec.decode(callback.data, callback.from_user.id)
            except CallbackAuthError:
                raise SkipHandler
        if not action.startswith(
            (
                "payreview:",
                "paygroup:",
                "orderpay:",
                "orderquote:",
                "buydraft:",
                "buygroup:",
                "buyout:add:",
                "orders_filter:",
                "my_orders:",
            )
        ):
            raise SkipHandler
        if action.startswith("buygroup:"):
            if action.startswith("buygroup:wait:"):
                await callback.answer("⏳ Заявка ожидает обработки цены", show_alert=True)
            else:
                await callback.answer()
            return
        if action.startswith("paygroup:"):
            if not await container.admin_service.is_admin(callback.from_user.id):
                await callback.answer("Только для админов", show_alert=True)
                return
            parts = action.split(":", maxsplit=2)
            if len(parts) != 3:
                await callback.answer("Некорректная команда", show_alert=True)
                return
            paygroup_action = parts[1]
            order_number = parts[2]
            order = await container.order_admin_service.get_order(order_number)
            if not order:
                await callback.answer("Заказ не найден", show_alert=True)
                return
            if paygroup_action == "approve":
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=OrderStatus.PAID,
                    changed_by_user_id=callback.from_user.id,
                    note="payment approved in group",
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
                    note="Платёж проверен и оплачен. В ближайшее время закажут товар.",
                )
                await callback.answer("Оплата подтверждена")
                await callback.message.edit_reply_markup(
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="✅ Оплачено", callback_data=callback_codec.encode_public("paygroup:noop"))]
                        ]
                    )
                )
                return
            if paygroup_action == "reject":
                await callback.answer()
                await callback.message.edit_reply_markup(
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="❓ Причина отмены?",
                                    callback_data=callback_codec.encode_public(
                                        f"paygroup:ask_reason:{order_number}",
                                    ),
                                )
                            ]
                        ]
                    )
                )
                return
            if paygroup_action == "ask_reason":
                await payment_reject_pending_store.set_pending(
                    chat_id=int(callback.message.chat.id),
                    message_id=int(callback.message.message_id),
                    order_number=order_number,
                )
                await callback.answer("Ответьте на это сообщение с причиной отмены", show_alert=True)
                return
            if paygroup_action == "noop":
                await callback.answer("Оплата уже подтверждена", show_alert=False)
                return
            await callback.answer("Неизвестное действие", show_alert=True)
            return
        if action.startswith("buyout:add:"):
            if await _is_blocked_user(callback.from_user.id):
                await callback.answer("Доступ ограничен", show_alert=True)
                return
            session = await container.profile_flow.get_or_create_session(platform, callback.from_user.id)
            if session.state != DialogState.BUYOUT_ADD_MORE:
                await callback.answer("Сессия устарела", show_alert=True)
                return
            if action == "buyout:add:more":
                response = await container.buyout_flow.start(session)
            elif action == "buyout:add:done":
                response = await container.buyout_flow.handle_text(session, "нет")
            else:
                await callback.answer("Некорректная команда", show_alert=True)
                return
            await callback.answer()
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            kwargs = {"parse_mode": "HTML"}
            if response.state == DialogState.BUYOUT_ADD_MORE:
                kwargs["reply_markup"] = buyout_add_more_inline_keyboard(
                    user_id=callback.from_user.id,
                    codec=callback_codec,
                )
            elif response.state == DialogState.IDLE:
                is_admin = await container.admin_service.is_admin(callback.from_user.id)
                kwargs["reply_markup"] = main_menu_keyboard(include_admin=is_admin)
            await callback.message.answer(response.text, **kwargs)
            return
        if action.startswith("buydraft:"):
            if not await container.admin_service.is_admin(callback.from_user.id):
                await callback.answer("Только для админов", show_alert=True)
                return
            parts = action.split(":", maxsplit=2)
            if len(parts) != 3:
                await callback.answer("Некорректная команда", show_alert=True)
                return
            draft_action = parts[1]
            draft_ref = parts[2]
            draft = await quote_draft_store.get_by_token(draft_ref)
            if not draft:
                draft = await quote_draft_store.get(draft_ref)
            if not draft:
                await callback.answer("Черновик цены не найден. Ответьте на заявку с ценой заново.", show_alert=True)
                return
            order_number = str(draft.get("order_number") or draft_ref).strip()
            order = await container.order_admin_service.get_order(order_number)
            if not order:
                await callback.answer("Заказ не найден", show_alert=True)
                return
            if draft_action == "send":
                price_rub = int(draft.get("price_rub") or order.price_rub or 0)
                manager_comment = str(draft.get("manager_comment") or order.manager_comment or "").strip()
                profile = await container.profile_repo.get_by_id(order.user_profile_id)
                if not profile or not profile.telegram_user_id:
                    await callback.answer("У клиента нет Telegram — отправить цену нельзя.", show_alert=True)
                    return
                if price_rub <= 0:
                    await callback.answer("Сначала укажите цену ответом на заявку.", show_alert=True)
                    return
                updated = await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=OrderStatus.WAITING_PAYMENT,
                    changed_by_user_id=callback.from_user.id,
                    note="quote sent to user",
                    platform=Platform.TELEGRAM,
                )
                if not updated:
                    await callback.answer("Не удалось обновить статус заказа.", show_alert=True)
                    return
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="💳 Оплатить",
                                callback_data=callback_codec.encode(
                                    f"orderquote:pay:{order_number}",
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
                    f"По заказу <b>№{_h(order_number)}</b>\n"
                    f"Стоимость: <b>{price_rub} ₽</b>."
                )
                if manager_comment:
                    text += f"\nКомментарий: {_h(manager_comment)}"
                text += "\n\n<b>Если есть вопросы по заказу пишите прямо сюда</b>"
                try:
                    await callback.bot.send_message(
                        chat_id=int(profile.telegram_user_id),
                        text=text,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                except Exception:
                    await callback.answer("Не удалось отправить клиенту. Клиент мог заблокировать бота.", show_alert=True)
                    return
                await callback.answer("Отправлено клиенту")
                group_text = _format_buyout_group_order_text(
                    order,
                    profile,
                    price_rub=price_rub,
                    manager_comment=manager_comment,
                    footer="📤 Отправлено клиенту",
                )
                try:
                    await callback.message.edit_text(
                        group_text,
                        parse_mode="HTML",
                        reply_markup=None,
                    )
                except Exception:
                    pass
                await quote_draft_store.clear(order_number)
                return
            if draft_action == "reset":
                profile = await container.profile_repo.get_by_id(order.user_profile_id)
                try:
                    await container.order_admin_service.update_order_field(order_number, "price_rub", "")
                    await container.order_admin_service.update_order_field(order_number, "manager_comment", "")
                except Exception:
                    await callback.answer("Не удалось сбросить", show_alert=True)
                    return
                await container.order_admin_service.set_status(
                    order_number=order_number,
                    new_status=OrderStatus.PENDING,
                    changed_by_user_id=callback.from_user.id,
                    note="price draft reset in group",
                    platform=Platform.TELEGRAM,
                )
                if profile:
                    revert_text = _format_buyout_group_order_text(order, profile)
                    try:
                        await callback.message.edit_text(
                            revert_text,
                            parse_mode="HTML",
                            reply_markup=_buyout_group_waiting_keyboard(order_number, callback_codec),
                        )
                    except Exception:
                        pass
                await quote_draft_store.clear(order_number)
                await callback.answer("Цена сброшена")
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
                price_rub = int(order.price_rub or 0)
                manager_comment = (order.manager_comment or "").strip()
                instruction = await payment_store.get_text()
                payment_media_items = await payment_store.get_media_items()
                pay_keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="💳 Оплатить",
                                callback_data=callback_codec.encode(
                                    f"orderquote:pay:{order_number}",
                                    callback.from_user.id,
                                ),
                            ),
                            InlineKeyboardButton(
                                text="❌ Отменить",
                                callback_data=callback_codec.encode(
                                    f"orderquote:cancel:{order_number}",
                                    callback.from_user.id,
                                ),
                            ),
                        ]
                    ]
                )
                pay_text = instruction
                if price_rub:
                    pay_text = (
                        f"Заказ <b>№{_h(order_number)}</b> — <b>{price_rub} ₽</b>.\n"
                        f"{instruction}\n\n"
                        "<b>Если есть вопросы по заказу пишите прямо сюда</b>"
                    )
                elif manager_comment:
                    pay_text += f"\n\nКомментарий: {_h(manager_comment)}"
                await callback.answer("Подтверждено")
                await callback.message.edit_text(
                    f"Вы подтвердили цену по заказу <b>{_h(order_number)}</b>.",
                    parse_mode="HTML",
                    reply_markup=None,
                )
                await callback.message.answer(pay_text, parse_mode="HTML", reply_markup=pay_keyboard)
                for payment_media in payment_media_items:
                    await send_stored_media_to_telegram(callback.bot, callback.from_user.id, payment_media)
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
                price_rub = int(order.price_rub or 0)
                manager_comment = (order.manager_comment or "").strip()
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
                pay_text = instruction
                if price_rub:
                    pay_text = (
                        f"Заказ <b>№{_h(order_number)}</b> — <b>{price_rub} ₽</b>.\n\n"
                        f"{instruction}\n\n"
                        "<b>Если есть вопросы по заказу пишите прямо сюда</b>"
                    )
                if manager_comment and price_rub:
                    pay_text = pay_text.replace(
                        f"{instruction}\n\n",
                        f"{instruction}\nКомментарий: {_h(manager_comment)}\n\n",
                        1,
                    )
                await callback.answer()
                await callback.message.edit_reply_markup(reply_markup=None)
                await callback.message.answer(pay_text, parse_mode="HTML", reply_markup=keyboard)
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
                    note="Платёж проверен и оплачен. В ближайшее время закажут товар.",
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
                    "Платёж будет проверен. После проверки будет заказан товар.",
                    parse_mode="HTML",
                )
                await _post_payment_check_to_group(
                    callback,
                    container=container,
                    group_topics_store=group_topics_store,
                    payment_target_store=payment_target_store,
                    notification_settings_store=notification_settings_store,
                    callback_codec=callback_codec,
                    order=updated,
                    profile=profile,
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
            await container.buyout_flow.prepare_preferences(session)
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
            page_match = re.search(r"Страница (\d+)/", callback.message.text or callback.message.caption or "")
            page = int(page_match.group(1)) if page_match else 1
            response = await container.buyout_flow.render_orders(session, page=page)
            filters = container.buyout_flow.filter_states(session)
            reply_markup = my_orders_message_keyboard(
                user_id=callback.from_user.id,
                current_page=int(response.state_data.get("page", 1)),
                total_pages=int(response.state_data.get("total_pages", 1)),
                filters=filters,
                codec=callback_codec,
            )
            await callback.answer("Фильтр обновлен")
            await callback.message.edit_text(response.text, parse_mode="HTML", reply_markup=reply_markup)
            return

        if not action.startswith("my_orders:"):
            return
        try:
            page = int(action.split(":", maxsplit=1)[1])
        except ValueError:
            await callback.answer()
            return
        session = await container.profile_flow.get_or_create_session(platform, callback.from_user.id)
        await container.buyout_flow.prepare_preferences(session)
        response = await container.buyout_flow.render_orders(session, page=page)
        filters = container.buyout_flow.filter_states(session)
        response.reply_markup = my_orders_message_keyboard(
            user_id=callback.from_user.id,
            current_page=int(response.state_data.get("page", 1)),
            total_pages=int(response.state_data.get("total_pages", 1)),
            filters=filters,
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
        archive_chat_id, archive_topic_id, archive_message_id = _capture_buyout_media_reference(message)
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

        topics = await group_topics_store.ensure_all_system_topics(message.bot)
        if not topics or "buyout" not in topics:
            await message.reply(
                "Тема «Выкупы» недоступна. Проверьте chat_id группы и права бота на управление темами."
            )
            return
        target_chat_id = int(topics["chat_id"])
        target_topic_id = int(topics["buyout"])
        if int(message.chat.id) != target_chat_id:
            return
        if message.message_thread_id != target_topic_id:
            return

        payment_topic_id = topics.get("payment")
        if payment_topic_id and int(message.chat.id) == target_chat_id and message.message_thread_id == int(payment_topic_id):
            pending = await payment_reject_pending_store.get(
                chat_id=int(message.chat.id),
                message_id=int(message.reply_to_message.message_id),
            )
            if pending:
                order_number = str(pending.get("order_number") or "").strip()
                if order_number:
                    order = await container.order_admin_service.get_order(order_number)
                    if order:
                        reason = message.text.strip()
                        updated = await container.order_admin_service.set_status(
                            order_number=order_number,
                            new_status=OrderStatus.CANCELLED,
                            changed_by_user_id=message.from_user.id,
                            note=f"payment rejected in group: {reason}",
                            platform=Platform.TELEGRAM,
                        )
                        if updated:
                            profile = await container.profile_repo.get_by_id(order.user_profile_id)
                            if profile:
                                await _notify_user_status_changed_by_bot(
                                    bot=message.bot,
                                    container=container,
                                    order=updated,
                                    status=OrderStatus.CANCELLED,
                                    note=f"Оплата отменена: {reason}",
                                )
                            try:
                                await message.bot.edit_message_reply_markup(
                                    chat_id=int(message.reply_to_message.chat.id),
                                    message_id=int(message.reply_to_message.message_id),
                                    reply_markup=InlineKeyboardMarkup(
                                        inline_keyboard=[
                                            [
                                                InlineKeyboardButton(
                                                    text="🛑 Отменен",
                                                    callback_data=callback_codec.encode_public("paygroup:noop"),
                                                )
                                            ]
                                        ]
                                    ),
                                )
                            except Exception:
                                pass
                            await payment_reject_pending_store.clear(
                                chat_id=int(message.chat.id),
                                message_id=int(message.reply_to_message.message_id),
                            )
                            await message.reply("Оплата отменена, клиент уведомлен.")
                            return

        source_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        order_number = _extract_order_number_from_text(source_text)
        if not order_number:
            return
        parsed = _parse_group_price_input(message.text)
        if not parsed:
            await message.reply(
                "Формат ответа:\n"
                "<code>2000</code>\n"
                "или две строки:\n"
                "<code>2000</code>\n"
                "<code>комментарий</code>",
                parse_mode="HTML",
            )
            return
        price_rub, comment = parsed

        order = await container.order_admin_service.get_order(order_number)
        if not order:
            await message.reply("Заказ не найден.")
            return
        profile = await container.profile_repo.get_by_id(order.user_profile_id)
        if not profile:
            await message.reply("Профиль не найден.")
            return
        try:
            await container.order_admin_service.update_order_field(order_number, "price_rub", str(price_rub))
            await container.order_admin_service.update_order_field(
                order_number,
                "manager_comment",
                comment or "",
            )
        except Exception:
            await message.reply("Не удалось сохранить цену.")
            return

        try:
            draft_token = await quote_draft_store.save(
                order_number=order_number,
                price_rub=price_rub,
                manager_comment=comment,
                manager_user_id=message.from_user.id,
                group_message_id=int(message.reply_to_message.message_id),
            )
        except Exception:
            await message.reply("Не удалось сохранить черновик цены.")
            return
        group_text = _format_buyout_group_order_text(
            order,
            profile,
            price_rub=price_rub,
            manager_comment=comment,
        )
        keyboard = _buyout_group_draft_keyboard(
            draft_token=draft_token,
            codec=callback_codec,
        )
        try:
            await message.bot.edit_message_text(
                chat_id=int(message.chat.id),
                message_id=int(message.reply_to_message.message_id),
                text=group_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            await message.reply(
                group_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        try:
            await message.delete()
        except Exception:
            pass

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
            sent_to_group = await _notify_new_buyout_order(
                message=message,
                container=container,
                group_topics_store=group_topics_store,
                payment_target_store=payment_target_store,
                notification_settings_store=notification_settings_store,
                callback_codec=callback_codec,
            )
            if not sent_to_group:
                await message.answer(
                    "Заявка сохранена, но не удалось отправить её в группу. "
                    "Проверьте, что группа настроена (chat_id) и у бота есть права на управление темами."
                )

    return router


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
    group_topics_store: GroupTopicsStore,
    payment_target_store: PaymentReviewTargetStore,
    notification_settings_store: NotificationSettingsStore,
    callback_codec: CallbackCodec,
) -> bool:
    if not message.from_user:
        return False
    profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, message.from_user.id)
    if not profile:
        return False
    orders = await container.buyout_repo.list_for_user(profile.id, limit=1, offset=0)
    if not orders:
        return False
    order = orders[0]
    topics = await group_topics_store.ensure_all_system_topics(message.bot)
    if not topics:
        return False
    await payment_target_store.set_target(
        chat_id=int(topics["chat_id"]),
        topic_id=int(topics["payment"]),
    )
    target_chat_id = int(topics["chat_id"])
    target_topic_id = int(topics["buyout"])
    text = _format_buyout_group_order_text(order, profile)
    keyboard = _buyout_group_waiting_keyboard(order.order_number, callback_codec)
    try:
        disable_notification = await notification_settings_store.should_disable_notification("user")
        sent = await message.bot.send_message(
            chat_id=target_chat_id,
            text=text,
            parse_mode="HTML",
            message_thread_id=target_topic_id,
            reply_markup=keyboard,
            disable_notification=disable_notification,
        )
        await _attach_buyout_media_to_group_message(
            bot=message.bot,
            container=container,
            order=order,
            target_chat_id=target_chat_id,
            target_topic_id=target_topic_id,
            anchor_message_id=int(sent.message_id),
        )
        return True
    except Exception:
        return False


def _buyout_group_waiting_keyboard(order_number: str, codec: CallbackCodec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏳ Ожидание",
                    callback_data=codec.encode_public(f"buygroup:wait:{order_number}"),
                )
            ]
        ]
    )


def _buyout_group_draft_keyboard(
    *,
    draft_token: str,
    codec: CallbackCodec,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Отправить",
                    callback_data=codec.encode_public(f"buydraft:send:{draft_token}"),
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=codec.encode_public(f"buydraft:reset:{draft_token}"),
                ),
            ]
        ]
    )


def _format_buyout_group_order_text(
    order,
    profile,
    *,
    price_rub: int | None = None,
    manager_comment: str | None = None,
    footer: str = "",
) -> str:
    lines = [
        f"<b>Выкуп №{_h(order.order_number)}</b>",
        _h(order.product_url),
        _h(order.quantity_text),
    ]
    if price_rub is not None:
        lines.append(f"Цена: <b>{price_rub} ₽</b>")
    if manager_comment:
        lines.append(f"Комментарий: {_h(manager_comment)}")
    if footer:
        lines.append(footer)
    elif price_rub is None:
        lines.append("")
        lines.append(
            "Ответьте на это сообщение:\n"
            "<code>2000</code> или две строки — цена и комментарий."
        )
    return "\n".join(lines)


async def _attach_buyout_media_to_group_message(
    *,
    bot,
    container: AppContainer,
    order,
    target_chat_id: int,
    target_topic_id: int,
    anchor_message_id: int,
) -> None:
    media_items = await container.buyout_repo.list_order_media(order.id)
    sources: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for item in media_items:
        if item.tg_chat_id and item.tg_message_id:
            key = (int(item.tg_chat_id), int(item.tg_message_id))
            if key not in seen:
                seen.add(key)
                sources.append(key)
    if not sources and order.media_storage_chat_id and order.media_storage_message_id:
        sources.append((int(order.media_storage_chat_id), int(order.media_storage_message_id)))
    for from_chat_id, from_message_id in sources:
        try:
            await bot.copy_message(
                chat_id=target_chat_id,
                from_chat_id=from_chat_id,
                message_id=from_message_id,
                message_thread_id=target_topic_id,
                reply_to_message_id=anchor_message_id,
            )
        except Exception:
            continue


async def _post_payment_check_to_group(
    callback: CallbackQuery,
    *,
    container: AppContainer,
    group_topics_store: GroupTopicsStore,
    payment_target_store: PaymentReviewTargetStore,
    notification_settings_store: NotificationSettingsStore,
    callback_codec: CallbackCodec,
    order,
    profile,
) -> None:
    topics = await group_topics_store.ensure_all_system_topics(callback.bot)
    if not topics:
        return
    await payment_target_store.set_target(
        chat_id=int(topics["chat_id"]),
        topic_id=int(topics["payment"]),
    )
    target_chat_id = int(topics["chat_id"])
    target_topic_id = int(topics["payment"])
    quote_body = (
        f"Выкуп №{_h(order.order_number)}\n"
        f"Клиент: {_h(profile.code)} / {_h(profile.name or 'без имени')}\n"
        f"Ссылка: {_h(order.product_url)}\n"
        f"Детали: {_h(order.quantity_text)}"
    )
    if order.price_rub:
        quote_body += f"\nЦена: {order.price_rub} ₽"
    if order.manager_comment:
        quote_body += f"\nКомментарий: {_h(order.manager_comment)}"
    text = f"{_omsk_now_text()}\n\n<blockquote expandable>{quote_body}</blockquote>"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Оплачено",
                    callback_data=callback_codec.encode_public(f"paygroup:approve:{order.order_number}"),
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=callback_codec.encode_public(f"paygroup:reject:{order.order_number}"),
                ),
            ]
        ]
    )
    try:
        silent = await notification_settings_store.should_disable_notification("button")
        await callback.bot.send_message(
            chat_id=target_chat_id,
            text=text,
            parse_mode="HTML",
            message_thread_id=target_topic_id,
            reply_markup=keyboard,
            disable_notification=silent,
        )
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


def _capture_buyout_media_reference(message: Message) -> tuple[int | None, int | None, int | None]:
    """Сохраняем ссылку на медиа в личном чате (без копирования в группу)."""
    if not message.chat or not message.message_id:
        return None, None, None
    return int(message.chat.id), None, int(message.message_id)


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
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return None
        price_part = lines[0]
        comment = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
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
