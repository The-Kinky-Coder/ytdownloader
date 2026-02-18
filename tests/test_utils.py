import unittest

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


if __name__ == "__main__":
    unittest.main()
