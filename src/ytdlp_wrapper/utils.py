"""Utility helpers for naming and metadata."""

from __future__ import annotations

import re


_INVALID_CHARS = re.compile(r"[<>:\\|?*\"\n\r\t]")
_MULTI_SPACE = re.compile(r"\s+")


def sanitize(value: str, max_length: int = 120) -> str:
    if not value:
        return "unknown"
    sanitized = _INVALID_CHARS.sub("-", value)
    sanitized = sanitized.replace("/", "-").replace("\\", "-")
    sanitized = _MULTI_SPACE.sub(" ", sanitized).strip(" .")
    if not sanitized:
        sanitized = "unknown"
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip(" .")
    return sanitized


def parse_artist_title(raw_title: str) -> tuple[str | None, str]:
    if not raw_title:
        return None, "Unknown Title"
    if " - " in raw_title:
        parts = raw_title.split(" - ", 1)
        return parts[0].strip() or None, parts[1].strip() or raw_title
    return None, raw_title.strip()


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
