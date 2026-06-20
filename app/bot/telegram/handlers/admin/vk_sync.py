from __future__ import annotations

import aiohttp

from aiogram.types import Message

async def _sync_vk_attachment_from_tg(
    message: Message,
    container: AppContainer,
    media_type: str,
    file_id: str,
) -> str | None:
    if container.settings.vk is None:
        return None
    media_bytes, filename = await _download_telegram_media(message, file_id)
    if media_bytes is None or not filename:
        return None
    token = container.settings.vk.bot_token
    if media_type == "photo":
        return await _vk_upload_photo(token=token, media_bytes=media_bytes, filename=filename)
    return await _vk_upload_doc(token=token, media_bytes=media_bytes, filename=filename)


async def _download_telegram_media(message: Message, file_id: str) -> tuple[bytes | None, str | None]:
    try:
        tg_file = await message.bot.get_file(file_id)
    except Exception:
        return None, None
    file_path = str(tg_file.file_path or "")
    if not file_path:
        return None, None
    filename = file_path.rsplit("/", maxsplit=1)[-1] or "media.bin"
    url = f"https://api.telegram.org/file/bot{message.bot.token}/{file_path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return None, None
                data = await response.read()
    except Exception:
        return None, None
    return data, filename


async def _vk_upload_photo(token: str, media_bytes: bytes, filename: str) -> str | None:
    upload = await _vk_api_call(token, "photos.getMessagesUploadServer", {})
    upload_url = str(upload.get("upload_url", "")).strip() if isinstance(upload, dict) else ""
    if not upload_url:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field("photo", media_bytes, filename=filename, content_type="application/octet-stream")
            async with session.post(upload_url, data=form) as response:
                payload = await response.json(content_type=None)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    save = await _vk_api_call(
        token,
        "photos.saveMessagesPhoto",
        {
            "server": payload.get("server"),
            "photo": payload.get("photo"),
            "hash": payload.get("hash"),
        },
    )
    if not isinstance(save, list) or not save:
        return None
    item = save[0]
    if not isinstance(item, dict):
        return None
    owner_id = item.get("owner_id")
    media_id = item.get("id")
    if owner_id is None or media_id is None:
        return None
    return f"photo{owner_id}_{media_id}"


async def _vk_upload_doc(token: str, media_bytes: bytes, filename: str) -> str | None:
    upload = await _vk_api_call(token, "docs.getMessagesUploadServer", {"type": "doc"})
    upload_url = str(upload.get("upload_url", "")).strip() if isinstance(upload, dict) else ""
    if not upload_url:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field("file", media_bytes, filename=filename, content_type="application/octet-stream")
            async with session.post(upload_url, data=form) as response:
                payload = await response.json(content_type=None)
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload.get("file"):
        return None
    save = await _vk_api_call(token, "docs.save", {"file": payload.get("file"), "title": filename})
    if not isinstance(save, dict):
        return None
    doc = save.get("doc")
    if not isinstance(doc, dict):
        return None
    owner_id = doc.get("owner_id")
    media_id = doc.get("id")
    if owner_id is None or media_id is None:
        return None
    return f"doc{owner_id}_{media_id}"


async def _vk_create_logs_chat(token: str) -> int | None:
    response = await _vk_api_call(token, "messages.createChat", {"title": "Логи"})
    if isinstance(response, int):
        chat_id = response
    elif isinstance(response, dict):
        chat_id = response.get("chat_id") or response.get("id")
    else:
        chat_id = None
    if not chat_id:
        return None
    return 2_000_000_000 + int(chat_id)


async def _vk_api_call(token: str, method: str, params: dict) -> object:
    api_url = f"https://api.vk.com/method/{method}"
    payload = dict(params)
    payload["access_token"] = token
    payload["v"] = "5.199"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, data=payload) as response:
                raw = await response.json(content_type=None)
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw.get("response", {})

