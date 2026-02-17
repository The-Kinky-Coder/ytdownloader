"""Metadata cache for yt-dlp JSON responses."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Config

DEFAULT_METADATA_CACHE_TTL_DAYS = 30


@dataclass(frozen=True)
class MetadataCache:
    cache_dir: Path
    ttl_days: int = DEFAULT_METADATA_CACHE_TTL_DAYS
    enabled: bool = True

    def cache_path(self, url: str) -> Path:
        key = _hash_url(_normalize_url(url))
        return self.cache_dir / f"{key}.json"

    def read(self, url: str, logger: logging.Logger | None = None) -> dict | None:
        if not self.enabled:
            return None
        normalized = _normalize_url(url)
        path = self.cache_path(normalized)
        if not path.exists():
            _log(logger, "Metadata cache miss (no entry): %s", path.name)
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            _log(logger, "Metadata cache miss (invalid JSON): %s", path.name)
            _safe_unlink(path, logger, exc)
            return None
        cached_at = _parse_cached_at(payload.get("cached_at"))
        if not cached_at:
            _log(logger, "Metadata cache miss (missing timestamp): %s", path.name)
            _safe_unlink(path, logger)
            return None
        if datetime.now(timezone.utc) - cached_at > timedelta(days=self.ttl_days):
            _log(logger, "Metadata cache miss (expired): %s", path.name)
            _safe_unlink(path, logger)
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            _log(logger, "Metadata cache miss (invalid data): %s", path.name)
            _safe_unlink(path, logger)
            return None
        _log(logger, "Metadata cache hit: %s", path.name)
        return data

    def write(self, url: str, data: dict, logger: logging.Logger | None = None) -> None:
        if not self.enabled:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        normalized = _normalize_url(url)
        payload = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "url": normalized,
            "data": data,
        }
        path = self.cache_path(normalized)
        _atomic_write_json(path, payload)
        _log(logger, "Metadata cache write: %s", path.name)

    def purge(self, logger: logging.Logger | None = None) -> int:
        if not self.cache_dir.exists():
            return 0
        count = 0
        for path in self.cache_dir.glob("*.json"):
            try:
                path.unlink()
                count += 1
            except Exception as exc:  # noqa: BLE001
                _log(logger, "Failed to delete cache entry %s: %s", path, exc)
        return count


def metadata_cache_from_config(config: Config) -> MetadataCache:
    return MetadataCache(
        cache_dir=config.metadata_cache_dir,
        ttl_days=config.metadata_cache_ttl_days,
        enabled=config.metadata_cache_enabled,
    )


def purge_metadata_cache(config: Config, logger: logging.Logger | None = None) -> int:
    cache = metadata_cache_from_config(config)
    return cache.purge(logger)


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    ignored = {
        "si",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "feature",
    }
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k not in ignored
    ]
    normalized = parsed._replace(query=urlencode(query))
    return urlunparse(normalized)


def _parse_cached_at(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _atomic_write_json(path: Path, payload: dict) -> None:
    temp_name = f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}"
    temp_path = path.with_name(temp_name)
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temp_path, path)


def _safe_unlink(
    path: Path, logger: logging.Logger | None, exc: Exception | None = None
) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception as unlink_exc:  # noqa: BLE001
        if exc:
            _log(logger, "Failed to delete cache entry %s: %s", path, unlink_exc)


def _log(logger: logging.Logger | None, message: str, *args: object) -> None:
    if logger:
        logger.info(message, *args)
