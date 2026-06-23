from __future__ import annotations

from aiogram.types import Message
from aiogram.utils.text_decorations import html_decoration


def extract_message_html(message: Message) -> str:
    text = message.text
    if text is None:
        return extract_caption_html(message)
    entities = message.entities or []
    if entities:
        return html_decoration.unparse(text=text, entities=entities).strip()
    return text.strip()


def extract_caption_html(message: Message) -> str:
    text = message.caption
    if not text:
        return ""
    entities = message.caption_entities or []
    if entities:
        return html_decoration.unparse(text=text, entities=entities).strip()
    return text.strip()
