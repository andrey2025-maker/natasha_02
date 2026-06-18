from __future__ import annotations

import base64
import hashlib
import hmac
import json
import zlib
from dataclasses import dataclass
from typing import Any


class InvalidCallbackSignatureError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class CallbackSigner:
    secret: str
    signature_bytes: int = 10

    def sign(self, payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        compressed = zlib.compress(serialized, level=9)
        if len(compressed) < len(serialized):
            payload_token = "z" + _b64url_encode(compressed)
        else:
            payload_token = "r" + _b64url_encode(serialized)
        signature = hmac.new(self.secret.encode("utf-8"), serialized, hashlib.sha256).digest()[: self.signature_bytes]
        signature_token = _b64url_encode(signature)
        return f"{payload_token}.{signature_token}"

    def verify(self, signed_payload: str) -> dict[str, Any]:
        if "." in signed_payload:
            return self._verify_compact(signed_payload)
        return self._verify_legacy_envelope(signed_payload)

    def _verify_compact(self, signed_payload: str) -> dict[str, Any]:
        try:
            payload_token, signature_token = signed_payload.split(".", maxsplit=1)
            mode = payload_token[:1]
            raw_token = payload_token[1:] if mode in {"r", "z"} else payload_token
            decoded = _b64url_decode(raw_token)
            if mode == "z":
                serialized = zlib.decompress(decoded)
            else:
                serialized = decoded
            payload = json.loads(serialized.decode("utf-8"))
            signature = _b64url_decode(signature_token)
        except (KeyError, ValueError, json.JSONDecodeError, zlib.error) as exc:
            raise InvalidCallbackSignatureError("Malformed callback payload") from exc

        expected = hmac.new(
            self.secret.encode("utf-8"),
            serialized,
            hashlib.sha256,
        ).digest()[: self.signature_bytes]
        if not hmac.compare_digest(signature, expected):
            raise InvalidCallbackSignatureError("Invalid callback signature")

        return payload

    def _verify_legacy_envelope(self, signed_payload: str) -> dict[str, Any]:
        try:
            decoded = base64.urlsafe_b64decode(signed_payload.encode("ascii"))
            envelope = json.loads(decoded.decode("utf-8"))
            payload = envelope["data"]
            signature = envelope["sig"]
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidCallbackSignatureError("Malformed callback payload") from exc

        expected = hmac.new(
            self.secret.encode("utf-8"),
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidCallbackSignatureError("Invalid callback signature")
        return payload


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))
