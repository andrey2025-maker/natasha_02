from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass

from app.security.callback_signer import CallbackSigner, InvalidCallbackSignatureError


class CallbackAuthError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class CallbackCodec:
    signer: CallbackSigner

    def encode(self, action: str, user_id: int) -> str:
        packed_action = _pack_action(action)
        user_token = _to_base36(user_id)
        body = f"{user_token};{packed_action}"
        signature = _sign(self.signer.secret, body)
        token = f"{body};{signature}"
        if len(token.encode("utf-8")) > 64:
            # As a safety fallback, use legacy signer path.
            return self.signer.sign({"a": action, "u": user_id})
        return token

    def decode(self, raw_data: str, user_id: int) -> str:
        parsed = _try_decode_short(self.signer.secret, raw_data)
        if parsed is not None:
            parsed_user_id, parsed_action = parsed
            if parsed_user_id != user_id:
                raise CallbackAuthError("Callback is not for this user")
            return parsed_action

        try:
            payload = self.signer.verify(raw_data)
        except InvalidCallbackSignatureError as exc:
            raise CallbackAuthError("Invalid callback signature") from exc

        if payload.get("u") != user_id:
            raise CallbackAuthError("Callback is not for this user")
        action = payload.get("a")
        if not isinstance(action, str) or not action:
            raise CallbackAuthError("Invalid callback action")
        return action


_ACTION_SHORT_MAP = {
    "admin": "a",
    "orders": "o",
    "order": "od",
    "profile": "p",
    "profiles": "ps",
    "blocks": "b",
    "blockpick": "bp",
    "broadcast": "br",
    "backup": "bk",
    "notify": "n",
    "admins": "am",
    "search": "s",
    "page": "pg",
    "toggle": "tg",
    "status": "st",
    "set_status": "ss",
    "bulk_field": "bf",
    "edit_field": "ef",
    "manager_comment": "mc",
    "quantity_text": "qt",
    "product_url": "pu",
    "track_number": "tn",
    "price_rub": "pr",
    "show_blocked": "sb",
    "show_unsubscribed": "su",
    "start_block": "stb",
    "start_unblock": "stub",
    "reset": "rs",
    "waiting_payment": "wp",
    "in_transit": "it",
    "pickup_point": "pp",
    "cancelled": "cl",
    "payreview": "prv",
    "approve": "ap",
    "reject": "rj",
    "my_orders": "mo",
    "orders_filter": "of",
    "faq": "fq",
    "profile:start_fill": "psf",
    "profile:start_sync": "psy",
    "profile:buyout_start": "pbs",
    "profile:buyout_orders": "pbo",
    "profile:buyout_filters": "pbf",
}
_ACTION_RESTORE_MAP = {value: key for key, value in _ACTION_SHORT_MAP.items()}


def _pack_action(action: str) -> str:
    direct = _ACTION_SHORT_MAP.get(action)
    if direct:
        return direct
    parts = action.split(":")
    packed_parts = [_ACTION_SHORT_MAP.get(part, part) for part in parts]
    return ":".join(packed_parts)


def _unpack_action(action: str) -> str:
    direct = _ACTION_RESTORE_MAP.get(action)
    if direct:
        return direct
    parts = action.split(":")
    restored_parts = [_ACTION_RESTORE_MAP.get(part, part) for part in parts]
    return ":".join(restored_parts)


def _to_base36(value: int) -> str:
    if value < 0:
        raise ValueError("Negative values are not supported")
    if value == 0:
        return "0"
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = []
    current = value
    while current:
        current, rem = divmod(current, 36)
        result.append(alphabet[rem])
    return "".join(reversed(result))


def _from_base36(value: str) -> int:
    return int(value, 36)


def _sign(secret: str, body: str, sig_bytes: int = 6) -> str:
    digest = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()[:sig_bytes]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _try_decode_short(secret: str, raw_data: str) -> tuple[int, str] | None:
    parts = raw_data.split(";")
    if len(parts) != 3:
        return None
    user_token, packed_action, signature = parts
    if not user_token or not packed_action or not signature:
        return None
    expected = _sign(secret, f"{user_token};{packed_action}")
    if not hmac.compare_digest(signature, expected):
        raise CallbackAuthError("Invalid callback signature")
    try:
        user_id = _from_base36(user_token)
    except ValueError as exc:
        raise CallbackAuthError("Invalid callback user id") from exc
    action = _unpack_action(packed_action)
    return user_id, action
