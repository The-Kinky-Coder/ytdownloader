import struct
import tempfile
import unittest
from pathlib import Path

from ytdlp_wrapper.config import Config
from ytdlp_wrapper.downloader import rewrite_m3u_from_dir, retag_playlist_dir


def _make_minimal_opus(path: Path) -> None:
    """Write a minimal valid Ogg Opus file (2 pages: ID header + comment header)."""

    def _ogg_page(
        serial: int, seqno: int, granule: int, payload: bytes, bos: bool = False
    ) -> bytes:
        flags = 0x02 if bos else 0x00
        lacing: list[int] = []
        rem = len(payload)
        while rem >= 255:
            lacing.append(255)
            rem -= 255
        lacing.append(rem)
        # Build page with zeroed CRC
        header = (
            b"OggS"
            + struct.pack("<BBqII", 0, flags, granule, serial, seqno)
            + b"\x00\x00\x00\x00"
            + struct.pack("<B", len(lacing))
            + bytes(lacing)
        )
        page_no_crc = header + payload
        import zlib

        crc = zlib.crc32(page_no_crc, 0) & 0xFFFFFFFF
        header_with_crc = (
            b"OggS"
            + struct.pack("<BBqII", 0, flags, granule, serial, seqno)
            + struct.pack("<I", crc)
            + struct.pack("<B", len(lacing))
            + bytes(lacing)
        )
        return header_with_crc + payload

    serial = 0xDEADBEEF
    opus_head = (
        b"OpusHead" + struct.pack("<BBHI", 1, 2, 312, 48000) + struct.pack("<hB", 0, 0)
    )
    vendor = b"test"
    opus_tags = (
        b"OpusTags" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", 0)
    )
    path.write_bytes(
        _ogg_page(serial, 0, 0, opus_head, bos=True)
        + _ogg_page(serial, 1, 0, opus_tags)
    )


class TestM3URewrite(unittest.TestCase):
    def test_rewrite_m3u_uses_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            playlist_dir = base_dir / "Brother Ali Mix"
            playlist_dir.mkdir(parents=True, exist_ok=True)

            # Files must use " - " separator so parse_artist_title works correctly
            (playlist_dir / "01-Artist - One.opus").write_text("", encoding="utf-8")
            (playlist_dir / "02-Artist - Two.opus").write_text("", encoding="utf-8")

            config = Config().with_overrides(base_dir=str(base_dir))
            rewrite_m3u_from_dir(playlist_dir, config, logger=_noop_logger())

            m3u_path = playlist_dir / "Brother Ali Mix.m3u"
            self.assertTrue(m3u_path.exists())
            lines = m3u_path.read_text(encoding="utf-8").splitlines()

            self.assertEqual(lines[0], "#EXTM3U")
            self.assertIn("#EXTINF:-1,Artist - One", lines)
            self.assertIn("Brother Ali Mix/01-Artist - One.opus", lines)
            self.assertIn("Brother Ali Mix/02-Artist - Two.opus", lines)


class TestRetagPlaylistDir(unittest.TestCase):
    """Test the retroactive retag feature (requires mutagen)."""

    def _mutagen_available(self) -> bool:
        try:
            import mutagen  # noqa: F401

            return True
        except ImportError:
            return False

    def test_retag_missing_dir_raises(self) -> None:
        config = Config().with_overrides(base_dir="/nonexistent")
        with self.assertRaises(Exception):
            retag_playlist_dir(
                Path("/nonexistent/NoSuchPlaylist"),
                config,
                logger=_noop_logger(),
            )

    def test_retag_empty_dir_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            playlist_dir = base_dir / "Empty Mix"
            playlist_dir.mkdir()
            config = Config().with_overrides(base_dir=str(base_dir))
            updated = retag_playlist_dir(playlist_dir, config, logger=_noop_logger())
            self.assertEqual(updated, 0)

    def test_retag_opus_files(self) -> None:
        """Retag should write ALBUMARTIST/ALBUM/COMPILATION to real Opus files."""
        if not self._mutagen_available():
            self.skipTest("mutagen not installed")

        try:
            from mutagen.oggopus import OggOpus
        except ImportError:
            self.skipTest("mutagen OggOpus not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            playlist_dir = base_dir / "Cyber Punk Mix 1"
            playlist_dir.mkdir()

            # Create minimal valid Opus files using raw Ogg bytes
            for stem in ["01-Artist A-Track1", "02-Artist B-Track2"]:
                p = playlist_dir / f"{stem}.opus"
                _make_minimal_opus(p)

            config = Config().with_overrides(base_dir=str(base_dir))
            updated = retag_playlist_dir(playlist_dir, config, logger=_noop_logger())
            self.assertEqual(updated, 2)

            # Verify tags were written
            for stem in ["01-Artist A-Track1", "02-Artist B-Track2"]:
                p = playlist_dir / f"{stem}.opus"
                ogg = OggOpus(p)
                self.assertEqual(ogg.get("albumartist", [None])[0], "Various Artists")
                self.assertEqual(ogg.get("album", [None])[0], "Cyber Punk Mix 1")
                self.assertEqual(ogg.get("compilation", [None])[0], "1")


class _noop_logger:
    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None
