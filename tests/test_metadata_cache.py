import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ytdlp_wrapper.metadata_cache import MetadataCache


class TestMetadataCache(unittest.TestCase):
    def test_write_and_read_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = MetadataCache(cache_dir=Path(temp_dir), ttl_days=30, enabled=True)
            url = "https://music.youtube.com/watch?v=abc123"
            payload = {"id": "abc123", "title": "Test"}

            cache.write(url, payload)
            loaded = cache.read(url)

            self.assertEqual(loaded, payload)

    def test_expired_entry_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            cache = MetadataCache(cache_dir=cache_dir, ttl_days=1, enabled=True)
            url = "https://music.youtube.com/watch?v=expired"
            entry = {
                "cached_at": (
                    datetime.now(timezone.utc) - timedelta(days=2)
                ).isoformat(),
                "url": url,
                "data": {"id": "expired"},
            }
            path = cache.cache_path(url)
            cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(entry), encoding="utf-8")

            loaded = cache.read(url)

            self.assertIsNone(loaded)
            self.assertFalse(path.exists())

    def test_disabled_cache_skips_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = MetadataCache(cache_dir=Path(temp_dir), ttl_days=30, enabled=False)
            url = "https://music.youtube.com/watch?v=skip"

            self.assertIsNone(cache.read(url))


if __name__ == "__main__":
    unittest.main()
