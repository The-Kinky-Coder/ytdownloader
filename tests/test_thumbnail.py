import logging
import threading
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from ytdlp_wrapper.downloader import (
    _download_thumbnail,
    _extract_embedded_art,
    generate_playlist_thumbnail,
)
from ytdlp_wrapper.config import Config
from ytdlp_wrapper.pending import audio_file_to_sidecar, PENDING_TASK_THUMBNAIL

class TestThumbnailHelpers(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmpdir.name)
        self.logger = logging.getLogger("test.thumbnail")
        self.logger.addHandler(logging.NullHandler())
        self.cfg = Config()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_download_thumbnail_success(self):
        url = "http://example.com/img.jpg"
        dest = self.base / "out.jpg"
        with patch("urllib.request.urlretrieve") as mock_retrieve:
            mock_retrieve.return_value = (str(dest), None)
            ok = _download_thumbnail(url, dest)
        self.assertTrue(ok)
        mock_retrieve.assert_called_once_with(url, str(dest))

    def test_download_thumbnail_fail(self):
        dest = self.base / "out.jpg"
        with patch("urllib.request.urlretrieve", side_effect=Exception("nah")):
            ok = _download_thumbnail("x", dest)
        self.assertFalse(ok)

    def test_extract_embedded_art_no_tags(self):
        p = self.base / "a.mp3"
        p.touch()
        ok = _extract_embedded_art(p, self.base / "out.jpg")
        self.assertFalse(ok)

    def test_generate_playlist_with_existing_folder(self):
        pd = self.base / "pl"
        pd.mkdir()
        (pd / "folder.jpg").write_text("x")
        generate_playlist_thumbnail(pd, self.cfg, self.logger)
        # nothing changed
        self.assertTrue((pd / "folder.jpg").exists())

    def test_generate_playlist_no_url_embedded(self):
        pd = self.base / "pl"
        pd.mkdir()
        # create first audio and patch _extract_embedded_art
        audio = pd / "001.mp3"
        audio.touch()
        def fake_extract(src, dest):
            # simulate writing
            dest.write_text("x")
            return True
        with patch("ytdlp_wrapper.downloader._extract_embedded_art", fake_extract):
            generate_playlist_thumbnail(pd, self.cfg, self.logger)
        self.assertTrue((pd / "folder.jpg").exists())

    def test_generate_playlist_url_thumbnail(self):
        pd = self.base / "pl"
        pd.mkdir()
        m3u = pd / "pl.m3u"
        m3u.write_text("#PLAYLIST-URL: http://dummy")
        # patch metadata fetch to produce thumbnail url
        with patch("ytdlp_wrapper.downloader.run_yt_dlp_json") as mock_meta, \
             patch("ytdlp_wrapper.downloader._download_thumbnail") as mock_dl:
            mock_meta.return_value = {"thumbnail": "http://img"}
            mock_dl.return_value = True
            generate_playlist_thumbnail(pd, self.cfg, self.logger)
        mock_dl.assert_called_once()

    def test_generate_playlist_fallback_to_pending(self):
        pd = self.base / "pl"
        pd.mkdir()
        # no m3u, no audio
        generate_playlist_thumbnail(pd, self.cfg, self.logger)
        # sidecar should be created
        pending = list(pd.rglob("*.pending.json"))
        self.assertTrue(pending)
        pf = pending[0]
        txt = pf.read_text()
        self.assertIn("thumbnail", txt)

    def test_generate_thumbnails_directory(self):
        pd = self.base / "pl"
        pd.mkdir()
        called = []
        def fake_gen(path, cfg, logger):
            called.append(path)
        from ytdlp_wrapper import downloader
        cfg2 = self.cfg.with_overrides(base_dir=str(self.base))
        class DummyProg:
            def __init__(self, total, logger):
                self.total = total
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                pass
            def add_task(self, key, label, total=None):
                pass
            def complete(self, key):
                pass
        orig_gen = downloader.generate_playlist_thumbnail
        orig_prog = downloader.ProgressReporter
        done = []
        def runner():
            try:
                downloader.generate_thumbnails(cfg2, self.logger, directory=pd)
            finally:
                done.append(True)
        t = threading.Thread(target=runner, daemon=True)
        try:
            downloader.generate_playlist_thumbnail = fake_gen
            downloader.ProgressReporter = DummyProg
            t.start()
            t.join(timeout=2)
        finally:
            downloader.generate_playlist_thumbnail = orig_gen
            downloader.ProgressReporter = orig_prog
        self.assertFalse(t.is_alive(), "generate_thumbnails(directory) hung")
        self.assertTrue(done, "worker thread never completed")
        self.assertEqual(called, [pd])

    def test_generate_thumbnails_all(self):
        pd1 = self.base / "a"
        pd2 = self.base / "b"
        pd1.mkdir()
        pd2.mkdir()
        called = []
        def fake_gen(path, cfg, logger):
            called.append(path)
        from ytdlp_wrapper import downloader
        cfg2 = self.cfg.with_overrides(base_dir=str(self.base))
        class DummyProg:
            def __init__(self, total, logger):
                self.total = total
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                pass
            def add_task(self, key, label, total=None):
                pass
            def complete(self, key):
                pass
        orig_gen = downloader.generate_playlist_thumbnail
        orig_prog = downloader.ProgressReporter
        done = []
        def runner():
            try:
                downloader.generate_thumbnails(cfg2, self.logger, directory=None)
            finally:
                done.append(True)
        t = threading.Thread(target=runner, daemon=True)
        try:
            downloader.generate_playlist_thumbnail = fake_gen
            downloader.ProgressReporter = DummyProg
            t.start()
            t.join(timeout=2)
        finally:
            downloader.generate_playlist_thumbnail = orig_gen
            downloader.ProgressReporter = orig_prog
        self.assertFalse(t.is_alive(), "generate_thumbnails(all) hung")
        self.assertTrue(done, "worker thread never completed")
        self.assertCountEqual(called, [pd1, pd2])

    def test_generate_thumbnails_progress(self):
        # verify that a ProgressReporter is used and receives the correct calls
        pd1 = self.base / "a"
        pd2 = self.base / "b"
        pd1.mkdir()
        pd2.mkdir()
        calls = []
        def fake_gen(path, cfg, logger):
            calls.append(path)
        from ytdlp_wrapper import downloader
        progress_instances: list = []
        class DummyProg:
            def __init__(self, total, logger):
                self.total = total
                self.events = []
                progress_instances.append(self)
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                pass
            def add_task(self, key, label, total=None):
                self.events.append(("add", key, label))
            def complete(self, key):
                self.events.append(("complete", key))
        cfg2 = self.cfg.with_overrides(base_dir=str(self.base))
        orig_gen = downloader.generate_playlist_thumbnail
        orig_prog = downloader.ProgressReporter
        done = []
        def runner():
            try:
                downloader.generate_thumbnails(cfg2, self.logger, directory=None)
            finally:
                done.append(True)
        t = threading.Thread(target=runner, daemon=True)
        try:
            downloader.generate_playlist_thumbnail = fake_gen
            downloader.ProgressReporter = DummyProg
            t.start()
            t.join(timeout=2)
        finally:
            downloader.generate_playlist_thumbnail = orig_gen
            downloader.ProgressReporter = orig_prog
        self.assertFalse(t.is_alive(), "generate_thumbnails(progress) hung")
        self.assertTrue(done, "worker thread never completed")
        # sanity checks
        self.assertCountEqual(calls, [pd1, pd2])
        self.assertEqual(len(progress_instances), 1)
        prog = progress_instances[0]
        self.assertEqual(prog.total, 2)
        # should have seen add/complete for each folder
        adds = [e for e in prog.events if e[0] == "add"]
        completes = [e for e in prog.events if e[0] == "complete"]
        self.assertEqual(len(adds), 2)
        self.assertEqual(len(completes), 2)

if __name__ == "__main__":
    unittest.main()
