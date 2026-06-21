from __future__ import annotations

from app.domain.models import BuyoutOrder


def normalize_track(value: str) -> str:
    return (value or "").strip().casefold()


def parse_txt_track_lines(content: str) -> list[str]:
    lines: list[str] = []
    for raw in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        track = raw.strip()
        if track:
            lines.append(track)
    return lines


def match_uploaded_tracks(
    uploaded: list[str],
    orders: list[BuyoutOrder],
) -> tuple[int, int, list[str]]:
    by_track: dict[str, str] = {}
    for order in orders:
        if not order.track_number:
            continue
        key = normalize_track(order.track_number)
        if key and key not in by_track:
            by_track[key] = order.order_number

    non_empty = [line for line in uploaded if normalize_track(line)]
    matched_count = 0
    matched_order_numbers: list[str] = []
    for line in non_empty:
        key = normalize_track(line)
        order_number = by_track.get(key)
        if order_number:
            matched_count += 1
            matched_order_numbers.append(order_number)

    unique_order_numbers = sorted(set(matched_order_numbers))
    return matched_count, len(non_empty), unique_order_numbers
