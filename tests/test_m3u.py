import tempfile
import unittest
from pathlib import Path

from ytdlp_wrapper.config import Config
from ytdlp_wrapper.downloader import rewrite_m3u_from_dir


class TestM3URewrite(unittest.TestCase):
    def test_rewrite_m3u_uses_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            playlist_dir = base_dir / "Brother Ali Mix"
            playlist_dir.mkdir(parents=True, exist_ok=True)

            (playlist_dir / "01-Artist-One.opus").write_text("", encoding="utf-8")
            (playlist_dir / "02-Artist-Two.opus").write_text("", encoding="utf-8")

            config = Config().with_overrides(base_dir=str(base_dir))
            rewrite_m3u_from_dir(playlist_dir, config, logger=_noop_logger())

            m3u_path = playlist_dir / "Brother Ali Mix.m3u"
            self.assertTrue(m3u_path.exists())
            lines = m3u_path.read_text(encoding="utf-8").splitlines()

            self.assertEqual(lines[0], "#EXTM3U")
            self.assertIn("#EXTINF:-1,Artist - One", lines)
            self.assertIn("Brother Ali Mix/01-Artist-One.opus", lines)
            self.assertIn("Brother Ali Mix/02-Artist-Two.opus", lines)


class _noop_logger:
    def info(self, *args, **kwargs) -> None:  # noqa: D401
        return None
