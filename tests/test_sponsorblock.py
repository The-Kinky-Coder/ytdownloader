"""Tests for SponsorBlock API error detection and retry logic."""

from __future__ import annotations

import unittest
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

from ytdlp_wrapper.downloader import (
    _is_sponsorblock_api_error,
    _SPONSORBLOCK_API_ERROR_PHRASE,
    _SPONSORBLOCK_RETRY_ATTEMPTS,
)


class TestIsSponsorblockApiError(unittest.TestCase):
    """Tests for _is_sponsorblock_api_error()."""

    def _make_lines(self, *lines: str) -> deque[str]:
        return deque(lines, maxlen=20)

    def test_detects_http_500_error(self) -> None:
        lines = self._make_lines(
            "[download] 100%",
            f"ERROR: Preprocessing: {_SPONSORBLOCK_API_ERROR_PHRASE}: HTTP Error 500: Internal Server Error",
        )
        self.assertTrue(_is_sponsorblock_api_error(lines))

    def test_detects_http_503_error(self) -> None:
        lines = self._make_lines(
            f"ERROR: {_SPONSORBLOCK_API_ERROR_PHRASE}: HTTP Error 503: Service Unavailable",
        )
        self.assertTrue(_is_sponsorblock_api_error(lines))

    def test_detects_connection_refused(self) -> None:
        lines = self._make_lines(
            f"WARNING: {_SPONSORBLOCK_API_ERROR_PHRASE}: Connection refused",
        )
        self.assertTrue(_is_sponsorblock_api_error(lines))

    def test_returns_false_for_unrelated_error(self) -> None:
        lines = self._make_lines(
            "ERROR: This video is unavailable.",
            "[download]   0% of 5.00MiB",
        )
        self.assertFalse(_is_sponsorblock_api_error(lines))

    def test_returns_false_for_empty_lines(self) -> None:
        self.assertFalse(_is_sponsorblock_api_error(deque()))

    def test_returns_false_for_normal_download_output(self) -> None:
        lines = self._make_lines(
            "[download]   50.0% of 4.32MiB at 1.20MiB/s",
            "[download] 100% of 4.32MiB",
            "[ExtractAudio] Destination: track.opus",
        )
        self.assertFalse(_is_sponsorblock_api_error(lines))

    def test_detects_phrase_anywhere_in_line(self) -> None:
        # Phrase could appear with different prefixes depending on yt-dlp version.
        lines = self._make_lines(
            f"  [SponsorBlock] {_SPONSORBLOCK_API_ERROR_PHRASE}: timeout",
        )
        self.assertTrue(_is_sponsorblock_api_error(lines))

    def test_phrase_is_case_sensitive(self) -> None:
        # The phrase is matched as-is (yt-dlp is consistent in its capitalisation).
        lines = self._make_lines(
            "ERROR: unable to communicate with sponsorblock api: HTTP Error 500",
        )
        self.assertFalse(_is_sponsorblock_api_error(lines))


class TestRetrySponsorblockForJob(unittest.TestCase):
    """Tests for _retry_sponsorblock_for_job()."""

    def _make_config(
        self, sponsorblock_categories: tuple[str, ...] = ("sponsor",)
    ) -> MagicMock:
        cfg = MagicMock()
        cfg.sponsorblock_categories = sponsorblock_categories
        cfg.sleep_interval = 0
        cfg.with_overrides.return_value = cfg
        return cfg

    def _make_job(self) -> MagicMock:
        job = MagicMock()
        job.output_stem = "001-Artist-Song"
        job.source_url = "https://youtube.com/watch?v=abc"
        job.meta.webpage_url = "https://youtube.com/watch?v=abc"
        return job

    @patch("ytdlp_wrapper.downloader._yt_dlp_args_reprocess")
    @patch("ytdlp_wrapper.downloader.subprocess.Popen")
    def test_returns_true_on_immediate_success(self, mock_popen, mock_args) -> None:
        import tempfile
        from ytdlp_wrapper.downloader import _retry_sponsorblock_for_job

        with tempfile.TemporaryDirectory() as real_tmp:
            # Place a fake processed audio file where the code will look for it.
            fake_output = Path(real_tmp) / "001-Artist-Song.opus"
            fake_output.touch()

            mock_args.return_value = ["yt-dlp", "--sponsorblock-remove", "sponsor"]

            proc = MagicMock()
            proc.stdout.__iter__ = lambda s: iter(["[download] 100%\n"])
            proc.wait.return_value = None
            proc.returncode = 0
            mock_popen.return_value = proc

            cfg = self._make_config()
            job = self._make_job()
            job.output_dir = Path(real_tmp)

            with patch(
                "ytdlp_wrapper.downloader.tempfile.TemporaryDirectory"
            ) as mock_tmpdir:
                mock_tmpdir.return_value.__enter__ = lambda s: real_tmp
                mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
                result = _retry_sponsorblock_for_job(cfg, job, MagicMock(), attempts=1)

        self.assertTrue(result)

    @patch("ytdlp_wrapper.downloader._yt_dlp_args_reprocess")
    @patch("ytdlp_wrapper.downloader.subprocess.Popen")
    @patch("ytdlp_wrapper.downloader.tempfile.TemporaryDirectory")
    def test_returns_false_after_all_attempts_fail(
        self, mock_tmpdir, mock_popen, mock_args
    ) -> None:
        import tempfile
        from ytdlp_wrapper.downloader import _retry_sponsorblock_for_job

        with tempfile.TemporaryDirectory() as real_tmp:
            mock_tmpdir.return_value.__enter__ = lambda s: real_tmp
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            mock_args.return_value = ["yt-dlp"]

            proc = MagicMock()
            proc.stdout.__iter__ = lambda s: iter(
                [f"ERROR: {_SPONSORBLOCK_API_ERROR_PHRASE}: HTTP Error 500\n"]
            )
            proc.wait.return_value = None
            proc.returncode = 1
            mock_popen.return_value = proc

            result = _retry_sponsorblock_for_job(
                self._make_config(), self._make_job(), MagicMock(), attempts=2
            )
        self.assertFalse(result)
        self.assertEqual(mock_popen.call_count, 2)

    @patch("ytdlp_wrapper.downloader._yt_dlp_args_reprocess")
    @patch("ytdlp_wrapper.downloader.subprocess.Popen")
    def test_returns_true_on_second_attempt(self, mock_popen, mock_args) -> None:
        import tempfile
        from ytdlp_wrapper.downloader import _retry_sponsorblock_for_job

        with tempfile.TemporaryDirectory() as real_tmp:
            fake_output = Path(real_tmp) / "001-Artist-Song.opus"
            fake_output.touch()
            mock_args.return_value = ["yt-dlp"]

            fail_proc = MagicMock()
            fail_proc.stdout.__iter__ = lambda s: iter(["ERROR: SponsorBlock failed\n"])
            fail_proc.wait.return_value = None
            fail_proc.returncode = 1

            ok_proc = MagicMock()
            ok_proc.stdout.__iter__ = lambda s: iter(["[download] 100%\n"])
            ok_proc.wait.return_value = None
            ok_proc.returncode = 0

            mock_popen.side_effect = [fail_proc, ok_proc]

            cfg = self._make_config()
            job = self._make_job()
            job.output_dir = Path(real_tmp)

            with patch(
                "ytdlp_wrapper.downloader.tempfile.TemporaryDirectory"
            ) as mock_tmpdir:
                mock_tmpdir.return_value.__enter__ = lambda s: real_tmp
                mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
                result = _retry_sponsorblock_for_job(cfg, job, MagicMock(), attempts=3)

        self.assertTrue(result)
        self.assertEqual(mock_popen.call_count, 2)


class TestBootstrapPendingFromLogs(unittest.TestCase):
    """Tests for _bootstrap_pending_from_logs() log parsing."""

    def _make_config(self, tmp_path: Path) -> MagicMock:
        cfg = MagicMock()
        cfg.log_dir = tmp_path
        cfg.base_dir = str(tmp_path)
        return cfg

    def _write_errors_log(self, log_dir: Path, lines: list[str]) -> None:
        (log_dir / "errors.log").write_text("\n".join(lines), encoding="utf-8")

    def _write_success_log(self, log_dir: Path, lines: list[str]) -> None:
        (log_dir / "success.log").write_text("\n".join(lines), encoding="utf-8")

    def test_bootstrap_parses_real_log_format(self) -> None:
        """The real errors.log format: TIMESTAMP stem | exit 1 | ERROR: ... | URL"""
        import tempfile
        from ytdlp_wrapper.downloader import _bootstrap_pending_from_logs

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = tmp / "048-Sleepermane & Aylior - Topic-Pebbles.opus"
            audio.touch()
            self._write_errors_log(
                tmp,
                [
                    "2026-02-19T15:06:05 048-Sleepermane & Aylior - Topic-Pebbles"
                    " | exit 1 | ERROR: Preprocessing: Unable to communicate with"
                    " SponsorBlock API: HTTP Error 500: Internal Server Error"
                    " | https://music.youtube.com/watch?v=YqivYZYykSo",
                ],
            )
            cfg = self._make_config(tmp)
            logger = MagicMock()
            created = _bootstrap_pending_from_logs(cfg, logger)
            self.assertEqual(created, 1)
            sidecar = tmp / "048-Sleepermane & Aylior - Topic-Pebbles.pending.json"
            self.assertTrue(sidecar.exists())
            import json

            data = json.loads(sidecar.read_text())
            self.assertEqual(
                data["source_url"], "https://music.youtube.com/watch?v=YqivYZYykSo"
            )
            self.assertIn("sponsorblock", data["pending"])

    def test_bootstrap_skips_when_no_audio_file_found(self) -> None:
        """If the audio file isn't on disk, no sidecar is created."""
        import tempfile
        from ytdlp_wrapper.downloader import _bootstrap_pending_from_logs

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # No audio file created.
            self._write_errors_log(
                tmp,
                [
                    "2026-02-19T15:06:05 048-Missing-Track"
                    " | exit 1 | ERROR: Preprocessing: Unable to communicate with"
                    " SponsorBlock API: HTTP Error 500"
                    " | https://music.youtube.com/watch?v=abc123",
                ],
            )
            cfg = self._make_config(tmp)
            created = _bootstrap_pending_from_logs(cfg, MagicMock())
            self.assertEqual(created, 0)

    def test_bootstrap_is_idempotent(self) -> None:
        """Running bootstrap twice does not duplicate or overwrite sidecars."""
        import tempfile
        from ytdlp_wrapper.downloader import _bootstrap_pending_from_logs

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = tmp / "001-Artist-Song.opus"
            audio.touch()
            self._write_errors_log(
                tmp,
                [
                    "2026-02-19T15:06:05 001-Artist-Song"
                    " | exit 1 | ERROR: Preprocessing: Unable to communicate with"
                    " SponsorBlock API: HTTP Error 500"
                    " | https://music.youtube.com/watch?v=xyz",
                ],
            )
            cfg = self._make_config(tmp)
            logger = MagicMock()
            first = _bootstrap_pending_from_logs(cfg, logger)
            second = _bootstrap_pending_from_logs(cfg, logger)
            self.assertEqual(first, 1)
            self.assertEqual(second, 0)  # sidecar already exists

    def test_bootstrap_returns_zero_with_no_errors_log(self) -> None:
        import tempfile
        from ytdlp_wrapper.downloader import _bootstrap_pending_from_logs

        with tempfile.TemporaryDirectory() as td:
            cfg = self._make_config(Path(td))
            self.assertEqual(_bootstrap_pending_from_logs(cfg, MagicMock()), 0)

    def test_bootstrap_also_accepts_legacy_marker(self) -> None:
        """Our own retry path logs a different string — both should be recognised."""
        import tempfile
        from ytdlp_wrapper.downloader import _bootstrap_pending_from_logs

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = tmp / "002-Artist-Song.opus"
            audio.touch()
            self._write_errors_log(
                tmp,
                [
                    "2026-02-19T15:10:00 002-Artist-Song"
                    " | SponsorBlock API unreachable after retries — segments not removed",
                ],
            )
            cfg = self._make_config(tmp)
            created = _bootstrap_pending_from_logs(cfg, MagicMock())
            self.assertEqual(created, 1)


if __name__ == "__main__":
    unittest.main()
