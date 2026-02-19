import tempfile
import unittest
from pathlib import Path

from ytdlp_wrapper.utils import parse_artist_title, safe_int, sanitize


class TestUtils(unittest.TestCase):
    def test_sanitize_basic(self) -> None:
        value = "Pink Floyd: Comfortably / Numb?"
        self.assertEqual(sanitize(value), "Pink Floyd- Comfortably - Numb-")

    def test_sanitize_empty(self) -> None:
        self.assertEqual(sanitize(""), "unknown")

    def test_parse_artist_title(self) -> None:
        artist, title = parse_artist_title("Artist - Song")
        self.assertEqual(artist, "Artist")
        self.assertEqual(title, "Song")

    def test_parse_artist_title_no_dash(self) -> None:
        artist, title = parse_artist_title("Song Only")
        self.assertIsNone(artist)
        self.assertEqual(title, "Song Only")

    def test_safe_int(self) -> None:
        self.assertEqual(safe_int("5"), 5)
        self.assertEqual(safe_int("bad", default=2), 2)


class TestFindExistingFile(unittest.TestCase):
    """Tests for downloader.find_existing_file() extension allowlist."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _touch(self, name: str) -> Path:
        p = self.tmp / name
        p.write_bytes(b"\x00")
        return p

    def test_returns_audio_file(self) -> None:
        from ytdlp_wrapper.downloader import find_existing_file

        self._touch("001-Artist-Song.opus")
        result = find_existing_file(self.tmp, "001-Artist-Song")
        self.assertIsNotNone(result)
        self.assertEqual(result.suffix, ".opus")  # type: ignore[union-attr]

    def test_ignores_webp_thumbnail(self) -> None:
        from ytdlp_wrapper.downloader import find_existing_file

        self._touch("001-Artist-Song.webp")
        result = find_existing_file(self.tmp, "001-Artist-Song")
        self.assertIsNone(result)

    def test_ignores_temp_audio_artifact(self) -> None:
        from ytdlp_wrapper.downloader import find_existing_file

        self._touch("001-Artist-Song.temp.opus")
        result = find_existing_file(self.tmp, "001-Artist-Song")
        self.assertIsNone(result)

    def test_ignores_pending_json(self) -> None:
        from ytdlp_wrapper.downloader import find_existing_file

        self._touch("001-Artist-Song.pending.json")
        result = find_existing_file(self.tmp, "001-Artist-Song")
        self.assertIsNone(result)

    def test_prefers_audio_over_artifacts_when_both_present(self) -> None:
        from ytdlp_wrapper.downloader import find_existing_file

        self._touch("001-Artist-Song.opus")
        self._touch("001-Artist-Song.webp")
        self._touch("001-Artist-Song.temp.opus")
        result = find_existing_file(self.tmp, "001-Artist-Song")
        self.assertIsNotNone(result)
        self.assertEqual(result.suffix, ".opus")  # type: ignore[union-attr]

    def test_returns_none_when_dir_does_not_exist(self) -> None:
        from ytdlp_wrapper.downloader import find_existing_file

        result = find_existing_file(self.tmp / "nonexistent", "001-Artist-Song")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
