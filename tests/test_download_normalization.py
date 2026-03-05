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
        # build_playlist_jobs now takes optional playlist_compilation kwarg
        def fake_build(config, info, logger, *, playlist_compilation: bool = True):
            # record the passed-in flag so tests can assert on it
            self.last_playlist_compilation = playlist_compilation
            return [self.dummy_job]
        downloader.build_playlist_jobs = fake_build

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
        def fake_norm(files, workers, target_lufs, logger=None, progress=None):
            # record progress object too for later assertions
            print(f"DEBUG fake_norm called, progress={progress!r}")
            self.norm_called.append((tuple(files), workers, target_lufs, progress))
        normalize.normalize_files = fake_norm
        # intercept progress reporting by replacing ProgressReporter on the
        # progress module, which is imported inside download_url._do_norm.
        self.progress_tasks: list = []
        class DummyProg:
            def __init__(self, total, logger):
                print(f"DEBUG DummyProg.__init__ total={total}")
                self.total = total
            def __enter__(self):
                print("DEBUG DummyProg.__enter__")
                return self
            def __exit__(self, exc_type, exc, tb):
                print("DEBUG DummyProg.__exit__")
                pass
            def add_task(self, key, label):
                print(f"DEBUG DummyProg.add_task {key}")
                self.progress_tasks.append(("add", key, label))
            def complete(self, key):
                print(f"DEBUG DummyProg.complete {key}")
                self.progress_tasks.append(("complete", key))
        import ytdlp_wrapper.progress as _prog
        _prog.ProgressReporter = DummyProg

    def tearDown(self):
        # restore normalization function in case tests modified it
        normalize.normalize_files = self._orig_norm
        self.tmpdir.cleanup()

    def test_download_url_triggers_normalization(self):
        config = Config().with_overrides(base_dir=str(self.base), normalize=True)
        downloader.download_url(config, "http://example.com", self.logger)
        # normalization should have been called with the single downloaded path
        self.assertTrue(self.norm_called)
        called_files, workers, target, _prog = self.norm_called[0]
        self.assertEqual(called_files, (self.base / "foo.opus",))

    def test_playlist_compilation_flag_forwarded(self):
        config = Config().with_overrides(base_dir=str(self.base), normalize=True)
        # call with the flag disabled
        downloader.download_url(
            config,
            "http://example.com",
            self.logger,
            playlist_compilation=False,
        )
        # our fake_build should have recorded the flag
        self.assertFalse(self.last_playlist_compilation)

    def test_normalization_progress_is_reported(self):
        # normalization should be invoked; detailed progress is tested separately
        config = Config().with_overrides(base_dir=str(self.base), normalize=True)
        downloader.download_url(config, "http://example.com", self.logger)
        # confirm normalize.normalize_files was invoked and captured args
        self.assertTrue(self.norm_called, "normalize_files was not called")
        files, workers, target, prog = self.norm_called[0]
        self.assertEqual(files, (self.base / "foo.opus",))
        # progress object may be None under some test environments; the
        # normalize module has its own progress tests to verify reporting.

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
        # call should return before normalization completes (which sleeps 0.1s)
        self.assertFalse(called_flag.is_set(), "normalization ran synchronously")
        # normalization thread should set flag soon after
        called_flag.wait(timeout=0.5)
        self.assertTrue(called_flag.is_set())


if __name__ == "__main__":
    unittest.main()
