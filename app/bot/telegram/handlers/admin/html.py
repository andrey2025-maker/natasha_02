from __future__ import annotations

from html import escape


def _h(value: object) -> str:
    if value is None:
        return "—"
    return escape(str(value), quote=False)
