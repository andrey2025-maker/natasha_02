from __future__ import annotations

from aiogram.types import Message


def extract_message_html(message: Message) -> str:
    if not message.text:
        return ""
    html_text = getattr(message, "html_text", None)
    if html_text:
        return html_text.strip()
    return message.text.strip()


def extract_caption_html(message: Message) -> str:
    if not message.caption:
        return ""
    html_text = getattr(message, "caption_html", None)
    if html_text:
        return html_text.strip()
    return message.caption.strip()
