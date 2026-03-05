import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ytdlp_wrapper import downloader, normalize
from ytdlp_wrapper.config import Config


class TestDownloadNormalizationIntegration(unittest.TestCase):
    def setUp(self):
        # create temporary base dir for downloads
        self.tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmpdir.name)

        # patch run_yt_dlp_json to return a playlist
        downloader.run_yt_dlp_json = lambda config, url, logger, extra_args=None: {
            "_type": "playlist",
            "entries": [{}],
        }

        # fake job class
        class DummyJob:
            output_dir = self.base
            output_stem = "foo"
            source_url = "https://music.example/watch?v=123"
            meta = type("M", (), {"compilation": False, "album_artist": None})
            m3u_path = None
        self.dummy_job = DummyJob()
        downloader.build_playlist_jobs = lambda config, info, logger: [self.dummy_job]

        # patch download_job to simply append a path and return it
        def fake_download_job(config, job, logger, progress, sponsorblock_retry_queue, downloaded_files=None):
            p = job.output_dir / "foo.opus"
            if downloaded_files is not None:
                downloaded_files.append(p)
            return p

        downloader.download_job = fake_download_job

        # silence logging during tests
        self.logger = logging.getLogger("test")
        self.logger.addHandler(logging.NullHandler())

        # intercept normalization calls
        self.norm_called = []
        self._orig_norm = normalize.normalize_files
        def fake_norm(files, workers, target_lufs, logger=None):
            self.norm_called.append((tuple(files), workers, target_lufs))
        normalize.normalize_files = fake_norm

    def tearDown(self):
        # restore normalization function in case tests modified it
        normalize.normalize_files = self._orig_norm
        self.tmpdir.cleanup()

    def test_download_url_triggers_normalization(self):
        config = Config().with_overrides(base_dir=str(self.base), normalize=True)
        downloader.download_url(config, "http://example.com", self.logger)
        # normalization should have been called with the single downloaded path
        self.assertTrue(self.norm_called)
        called_files, workers, target = self.norm_called[0]
        self.assertEqual(called_files, (self.base / "foo.opus",))

    def test_background_mode_runs_later(self):
        config = Config().with_overrides(
            base_dir=str(self.base), normalize=True, normalize_background=True
        )
        called_flag = threading.Event()

        def fake_norm(files, workers, target_lufs, logger=None):
            # delay and then set flag
            time.sleep(0.1)
            called_flag.set()
        normalize.normalize_files = fake_norm

        start = time.time()
        downloader.download_url(config, "http://example.com", self.logger)
        duration = time.time() - start
        # should return quickly (much less than 0.1s)
        self.assertLess(duration, 0.05)
        # normalization thread should set flag soon after
        called_flag.wait(timeout=0.5)
        self.assertTrue(called_flag.is_set())


if __name__ == "__main__":
    unittest.main()
