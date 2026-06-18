from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from html import unescape

from aiogram import Bot as TgBot
from aiogram.exceptions import TelegramForbiddenError
from app.bot.texts import messages as msg
from app.bot.telegram.callbacks import CallbackCodec
from app.bot.vk.keyboards import (
    buyout_add_more_keyboard,
    code_confirm_keyboard,
    profile_confirm_keyboard,
    profile_menu_keyboard,
    start_keyboard,
    status_filters_keyboard,
    yes_no_keyboard,
)
from app.core.container import AppContainer
from app.domain.enums import DialogState, Platform
from app.domain.models import OutboundMessage
from app.services.admin_tools_service import GroupTopicsStore, ProhibitedGoodsStore, StaticContentStore
from app.services.admin_tools_service import FaqMediaStore
from app.services.outbound_dispatcher import OutboundDispatcher, OutboundSender

logger = logging.getLogger(__name__)


class VkOutboundSender(OutboundSender):
    def __init__(self, api: object) -> None:
        self._api = api

    async def send(self, message: OutboundMessage) -> None:
        payload = message.payload
        if message.message_type == "sync_code":
            text = msg.sync_code_for_other_platform(
                code=str(payload.get("code", "")),
                profile_code=str(payload.get("profile_code", "")),
                from_platform=str(payload.get("from_platform", "")),
            )
        elif message.message_type in {"broadcast_text", "plain_text"}:
            text = str(payload.get("text", "")).strip()
            if not text:
                return
        else:
            return
        await self._api.messages.send(
            user_id=message.platform_user_id,
            random_id=0,
            message=_vk_text(text),
        )


async def run_vk_outbox_worker(container: AppContainer, poll_interval_seconds: float = 2.0) -> None:
    if container.settings.vk is None:
        logger.info("VK is not configured, VK outbox worker disabled")
        return
    try:
        from vkbottle import API
    except ModuleNotFoundError:
        logger.warning("vkbottle is not installed, VK outbox worker disabled")
        return

    dispatcher = OutboundDispatcher(container.outbound_repo)
    api = API(token=container.settings.vk.bot_token)
    sender = VkOutboundSender(api)
    logger.info("VK outbox worker started")
    while True:
        sent = await dispatcher.dispatch_pending(platform=Platform.VK, sender=sender, batch_size=50)
        if sent:
            logger.info("VK outbox delivered %s messages", sent)
        await asyncio.sleep(poll_interval_seconds)


async def run_vk_bot(container: AppContainer) -> None:
    if container.settings.vk is None:
        logger.info("VK incoming bot disabled: VK settings are empty")
        return

    try:
        from vkbottle.bot import Bot as VkBot
    except ModuleNotFoundError:
        logger.warning("vkbottle is not installed, VK incoming bot disabled")
        return

    vk_bot = VkBot(token=container.settings.vk.bot_token)
    callback_codec = CallbackCodec(container.callback_signer)
    tg_bot = TgBot(token=container.settings.telegram.bot_token)
    prohibited_store = ProhibitedGoodsStore(container.settings.database.dsn)
    group_topics_store = GroupTopicsStore(container.settings.database.dsn)
    faq_media_store = FaqMediaStore(container.settings.database.dsn)
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

    @vk_bot.on.message()
    async def handle_vk_message(message) -> None:
        text = (message.text or "").strip()
        user_id = int(message.from_id)
        if not text and not getattr(message, "attachments", None):
            return

        user_key = f"vk:{user_id}"
        if text and not container.rate_limiter.allow_request(user_key, text):
            return
        if not text and getattr(message, "attachments", None):
            if not container.rate_limiter.allow_request(user_key, "<media>"):
                return
        if text and not container.rate_limiter.validate_user_payload_size(len(text)):
            return

        session = await container.profile_flow.get_or_create_session(Platform.VK, user_id)
        vk_profile = await container.profile_repo.get_by_platform_user(Platform.VK, user_id)
        if vk_profile and vk_profile.is_blocked_by_admin:
            await message.answer("Ваш доступ ограничен администратором. Обратитесь в поддержку.")
            return

        if text.lower() in {"start", "/start", "начать"}:
            await message.answer(
                _vk_text(msg.welcome_text()),
                keyboard=start_keyboard(),
            )
            return

        if text == "Профиль":
            response = await container.profile_flow.show_profile_menu(session, other_platform_label="ТГ")
            await _dispatch_outbound_vk(container, tg_bot, response)
            await message.answer(_vk_text(response.text), keyboard=profile_menu_keyboard())
            return

        if text == "Вопросы":
            roots = await container.faq_service.list_children(parent_id=None)
            if not roots:
                await message.answer("Раздел вопросов пока пуст.")
                return
            lines = ["Разделы FAQ:"]
            lines.extend([f"{item.id}. {item.title}" for item in roots])
            lines.append("Напишите: faq <id>")
            await message.answer("\n".join(lines))
            return

        if text == "Запрещенные товары":
            await _send_static_content_vk(message, prohibited_store)
            return

        if text == "Как работает доставка":
            await _send_static_content_vk(message, delivery_store)
            return

        if text == "Наши контакты":
            await _send_static_content_vk(message, contacts_store)
            return

        if text.lower().startswith("faq "):
            try:
                section_id = int(text.split(maxsplit=1)[1])
            except (IndexError, ValueError):
                await message.answer("Формат: faq <id>")
                return
            section = await container.faq_service.get_section(section_id)
            if not section:
                await message.answer("Раздел не найден.")
                return
            children = await container.faq_service.list_children(section_id)
            lines = [section.title]
            if section.content_text:
                lines.append(section.content_text)
            if children:
                lines.append("")
                lines.append("Подразделы:")
                lines.extend([f"{item.id}. {item.title}" for item in children])
                lines.append("Напишите: faq <id>")
            media_items = await faq_media_store.get_media_items(section_id)
            attachments = [str(item.get("vk_attachment")) for item in media_items if item.get("vk_attachment")]
            if attachments:
                await message.answer(
                    _vk_text("\n".join(lines)),
                    attachment=",".join(attachments),
                )
            else:
                await message.answer(_vk_text("\n".join(lines)))
            return

        if text == "Заполнить профиль":
            response = await container.profile_flow.start_fill(session)
            await message.answer(_vk_text(response.text))
            return

        if text == "Есть профиль ТГ":
            response = await container.profile_flow.start_sync_with_other_platform(session)
            await message.answer(_vk_text(response.text))
            return

        profile_state = session.state
        if profile_state == DialogState.PROFILE_CONFIRM and text in {"Да", "Имя", "Тел.", "Город"}:
            action_map = {"Да": "confirm_yes", "Имя": "edit_name", "Тел.": "edit_phone", "Город": "edit_city"}
            response = await container.profile_flow.handle_callback(session, action_map[text], callback_codec)
            await _dispatch_outbound_vk(container, tg_bot, response)
            keyboard = None
            if response.state == DialogState.PROFILE_ASK_HAS_CODE:
                keyboard = yes_no_keyboard()
            await message.answer(_vk_text(response.text), keyboard=keyboard)
            return

        if profile_state == DialogState.PROFILE_ASK_HAS_CODE and text in {"Да", "Нет"}:
            action = "has_code_yes" if text == "Да" else "has_code_no"
            response = await container.profile_flow.handle_callback(session, action, callback_codec)
            await _dispatch_outbound_vk(container, tg_bot, response)
            keyboard = yes_no_keyboard() if response.state == DialogState.PROFILE_ASK_PASSPORT else None
            await message.answer(_vk_text(response.text), keyboard=keyboard)
            return

        if profile_state == DialogState.PROFILE_CONFIRM_CODE and text in {"Да", "Нет", "Исправить"}:
            action = "code_confirm" if text == "Да" else "code_fix"
            response = await container.profile_flow.handle_callback(session, action, callback_codec)
            await _dispatch_outbound_vk(container, tg_bot, response)
            keyboard = yes_no_keyboard() if response.state == DialogState.PROFILE_ASK_PASSPORT else None
            await message.answer(_vk_text(response.text), keyboard=keyboard)
            return

        if profile_state == DialogState.PROFILE_ASK_PASSPORT and text in {"Да", "Нет"}:
            action = "passport_yes" if text == "Да" else "passport_no"
            response = await container.profile_flow.handle_callback(session, action, callback_codec)
            await _dispatch_outbound_vk(container, tg_bot, response)
            await message.answer(_vk_text(response.text), keyboard=start_keyboard())
            return

        if text == "Заказ выкупа":
            response = await container.buyout_flow.start(session)
            await message.answer(_vk_text(response.text))
            return

        if text == "Мои заказы":
            response = await container.buyout_flow.render_orders(session, page=1)
            await message.answer(_vk_text(response.text), keyboard=status_filters_keyboard(container.buyout_flow.filter_states(session)))
            return

        if text == "Фильтры заказов":
            await container.buyout_flow.prepare_preferences(session)
            await message.answer(
                _vk_text(container.buyout_flow.filters_hint_text(session)),
                keyboard=status_filters_keyboard(container.buyout_flow.filter_states(session)),
            )
            return

        if text.lower().startswith("фильтр "):
            await container.buyout_flow.prepare_preferences(session)
            value = _normalize_filter_value(text[7:].strip())
            if value.lower() in {"все", "all"}:
                await container.buyout_flow.reset_status_filters(session)
                await message.answer(
                    _vk_text(container.buyout_flow.filters_hint_text(session)),
                    keyboard=status_filters_keyboard(container.buyout_flow.filter_states(session)),
                )
                return
            status = container.buyout_flow.parse_filter_alias(value)
            if status is None:
                await message.answer("Неизвестный статус фильтра. Пример: фильтр ожидание")
                return
            await container.buyout_flow.toggle_status_filter(session, status)
            await message.answer(
                _vk_text(container.buyout_flow.filters_hint_text(session)),
                keyboard=status_filters_keyboard(container.buyout_flow.filter_states(session)),
            )
            return

        if session.state == DialogState.BUYOUT_WAIT_MEDIA and getattr(message, "attachments", None):
            vk_attachment = _extract_vk_attachment_ids(getattr(message, "attachments", None))
            archive_chat_id, archive_topic_id, archive_message_id = await _archive_vk_media_in_tg(
                tg_bot=tg_bot,
                group_topics_store=group_topics_store,
                vk_user_id=user_id,
                attachments=getattr(message, "attachments", None),
            )
            media_items = _build_vk_order_media_items(
                getattr(message, "attachments", None),
                archive_chat_id=archive_chat_id,
                archive_topic_id=archive_topic_id,
                archive_message_id=archive_message_id,
            )
            response = await container.buyout_flow.handle_media(
                session,
                media_group_id=None,
                storage_chat_id=archive_chat_id,
                storage_topic_id=archive_topic_id,
                storage_message_id=archive_message_id,
                vk_attachment=vk_attachment,
                media_items=media_items,
            )
            await message.answer(_vk_text(response.text))
            return

        if session.state in {
            DialogState.BUYOUT_WAIT_LINK,
            DialogState.BUYOUT_WAIT_DETAILS,
            DialogState.BUYOUT_ADD_MORE,
        }:
            response = await container.buyout_flow.handle_text(session, text)
            keyboard = buyout_add_more_keyboard() if response.state == DialogState.BUYOUT_ADD_MORE else None
            await message.answer(_vk_text(response.text), keyboard=keyboard)
            return

        if session.state != DialogState.IDLE and text:
            response = await container.profile_flow.handle_text(session, text, callback_codec=callback_codec)
            await _dispatch_outbound_vk(container, tg_bot, response)
            keyboard = _keyboard_for_profile_state(response.state)
            await message.answer(_vk_text(response.text), keyboard=keyboard)
            return

        if text:
            await message.answer(
                "Команда не распознана. Напишите: Профиль, Заказ выкупа, Мои заказы, Фильтры заказов или /start."
            )

    logger.info("VK incoming bot started")
    await asyncio.to_thread(vk_bot.run_forever)


async def _dispatch_outbound_vk(container: AppContainer, tg_bot: TgBot, response) -> None:
    for outgoing in response.outbound_messages:
        target_platform = Platform(str(outgoing["platform"]))
        target_user_id = int(outgoing["platform_user_id"])
        payload = dict(outgoing["payload"])
        message_type = str(outgoing["message_type"])
        if target_platform == Platform.TELEGRAM:
            try:
                await tg_bot.send_message(
                    chat_id=target_user_id,
                    text=msg.sync_code_for_other_platform(
                        code=str(payload.get("code", "")),
                        profile_code=str(payload.get("profile_code", "")),
                        from_platform=str(payload.get("from_platform", "")),
                    ),
                    parse_mode="HTML",
                )
            except TelegramForbiddenError:
                profile = await container.profile_repo.get_by_platform_user(Platform.TELEGRAM, target_user_id)
                if profile and not profile.blocked_bot:
                    profile.blocked_bot = True
                    await container.profile_repo.save(profile)
            continue
        await container.outbound_repo.enqueue(
            OutboundMessage(
                id=0,
                platform=target_platform,
                platform_user_id=target_user_id,
                message_type=message_type,
                payload=payload,
            )
        )


_TAG_RE = re.compile(r"<[^>]+>")


def _vk_text(source: str) -> str:
    clean = _TAG_RE.sub("", source)
    return unescape(clean)


def _keyboard_for_profile_state(state: DialogState) -> str | None:
    if state == DialogState.PROFILE_CONFIRM:
        return profile_confirm_keyboard()
    if state == DialogState.PROFILE_ASK_HAS_CODE:
        return yes_no_keyboard()
    if state == DialogState.PROFILE_CONFIRM_CODE:
        return code_confirm_keyboard()
    if state == DialogState.PROFILE_ASK_PASSPORT:
        return yes_no_keyboard()
    return None


def _normalize_filter_value(value: str) -> str:
    cleaned = value.strip()
    if cleaned.lower().startswith(("🟢", "🔴")):
        cleaned = cleaned[1:].strip()
    if cleaned.lower().startswith("фильтр "):
        cleaned = cleaned[7:].strip()
    return cleaned


async def _send_static_content_vk(message, store: StaticContentStore) -> None:
    text = await store.get_text()
    media_items = await store.get_media_items()
    attachments = [str(item.get("vk_attachment")) for item in media_items if item.get("vk_attachment")]
    if attachments:
        full_text = text
        captions = [str(item.get("caption", "")).strip() for item in media_items]
        merged_caption = "\n".join([item for item in captions if item])
        if merged_caption:
            full_text += f"\n\n{merged_caption}"
        await message.answer(
            _vk_text(full_text),
            attachment=",".join(attachments),
        )
        return
    await message.answer(_vk_text(text))
    if media_items:
        await message.answer(
            "К этому разделу привязан медиа-материал, но VK вложение ещё не синхронизировано."
        )


def _extract_vk_attachment_ids(attachments: Any) -> str | None:
    tokens: list[str] = []
    for item in _iter_attachments(attachments):
        token = _attachment_to_token(item)
        if token:
            tokens.append(token)
    if not tokens:
        return None
    return ",".join(tokens)


def _attachment_to_token(item: Any) -> str | None:
    data_type = _pick_attr(item, "type")
    if not data_type:
        return None
    payload = _pick_attr(item, data_type)
    if payload is None:
        payload = item
    owner_id = _pick_attr(payload, "owner_id")
    media_id = _pick_attr(payload, "id")
    if owner_id is None or media_id is None:
        return None
    return f"{data_type}{owner_id}_{media_id}"


def _pick_attr(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _extract_vk_photo_urls(attachments: Any) -> list[str]:
    urls: list[str] = []
    for item in _iter_attachments(attachments):
        data_type = _pick_attr(item, "type")
        if data_type != "photo":
            continue
        photo = _pick_attr(item, "photo")
        if photo is None and isinstance(item, dict):
            photo = item.get("photo")
        sizes = _pick_attr(photo, "sizes") or []
        best_url: str | None = None
        best_area = -1
        for size in list(sizes):
            url = str(_pick_attr(size, "url") or "").strip()
            width = int(_pick_attr(size, "width") or 0)
            height = int(_pick_attr(size, "height") or 0)
            area = width * height
            if url and area >= best_area:
                best_url = url
                best_area = area
        if best_url:
            urls.append(best_url)
    return urls


def _iter_attachments(attachments: Any) -> list[Any]:
    if not attachments:
        return []
    if isinstance(attachments, list):
        return attachments
    try:
        return list(attachments)
    except TypeError:
        return [attachments]


def _build_vk_order_media_items(
    attachments: Any,
    archive_chat_id: int | None,
    archive_topic_id: int | None,
    archive_message_id: int | None,
) -> list[dict]:
    items: list[dict] = []
    for attachment in _iter_attachments(attachments):
        token = _attachment_to_token(attachment)
        media_type = str(_pick_attr(attachment, "type") or "unknown")
        items.append(
            {
                "platform": Platform.VK.value,
                "media_type": media_type,
                "tg_chat_id": int(archive_chat_id) if archive_chat_id else None,
                "tg_topic_id": int(archive_topic_id) if archive_topic_id else None,
                "tg_message_id": int(archive_message_id) if archive_message_id else None,
                "tg_file_id": None,
                "vk_attachment": token,
            }
        )
    return items


async def _archive_vk_media_in_tg(
    tg_bot: TgBot,
    group_topics_store: GroupTopicsStore,
    vk_user_id: int,
    attachments: Any,
) -> tuple[int | None, int | None, int | None]:
    target_chat_id, target_topic_id = await group_topics_store.get_tg_topic("logs")
    if not target_chat_id:
        return None, None, None
    first_message_id: int | None = None
    photo_urls = _extract_vk_photo_urls(attachments)
    for url in photo_urls:
        try:
            sent = await tg_bot.send_photo(
                chat_id=target_chat_id,
                photo=url,
                message_thread_id=target_topic_id,
                caption=f"VK media archive user={vk_user_id}",
            )
            if first_message_id is None:
                first_message_id = int(sent.message_id)
        except Exception:
            continue
    token_text = _extract_vk_attachment_ids(attachments) or "no-attachments"
    if first_message_id is None:
        try:
            sent = await tg_bot.send_message(
                chat_id=target_chat_id,
                text=f"VK media archive user={vk_user_id}\n{token_text}",
                message_thread_id=target_topic_id,
            )
            first_message_id = int(sent.message_id)
        except Exception:
            return None, None, None
    return int(target_chat_id), int(target_topic_id) if target_topic_id else None, first_message_id
