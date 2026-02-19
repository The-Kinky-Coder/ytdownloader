"""Tests for SponsorBlock API error detection and retry logic."""

from __future__ import annotations

import json
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error

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


# ---------------------------------------------------------------------------
# Tests for sponsorblock_local module
# ---------------------------------------------------------------------------


class TestExtractVideoId(unittest.TestCase):
    """Tests for sponsorblock_local.extract_video_id()."""

    def _fn(self, url: str):
        from ytdlp_wrapper.sponsorblock_local import extract_video_id

        return extract_video_id(url)

    def test_standard_watch_url(self) -> None:
        self.assertEqual(
            self._fn("https://music.youtube.com/watch?v=YqivYZYykSo"),
            "YqivYZYykSo",
        )

    def test_standard_youtube_url(self) -> None:
        self.assertEqual(
            self._fn("https://www.youtube.com/watch?v=abc123&si=tracking"),
            "abc123",
        )

    def test_short_link(self) -> None:
        self.assertEqual(
            self._fn("https://youtu.be/YqivYZYykSo"),
            "YqivYZYykSo",
        )

    def test_returns_none_for_url_with_no_id(self) -> None:
        from ytdlp_wrapper.sponsorblock_local import extract_video_id

        # A URL with no path and no query params returns None
        result = extract_video_id("https://example.com/")
        self.assertIsNone(result)

    def test_playlist_url_without_v_param(self) -> None:
        # Playlist URLs only have 'list', no 'v'.
        # extract_video_id falls through to the path segment ("playlist") since
        # there is no 'v' query param — callers are expected to check for a
        # meaningful video ID before using it.
        result = self._fn("https://music.youtube.com/playlist?list=PLabc")
        # The path is "playlist" — not None, but not a real video ID.
        # The important contract: no 'v' param → result is NOT a video ID.
        # We just verify the function doesn't crash and returns the path segment.
        self.assertEqual(result, "playlist")


class TestFetchSegments(unittest.TestCase):
    """Tests for sponsorblock_local.fetch_segments()."""

    def _fn(self, video_id, categories, **kw):
        from ytdlp_wrapper.sponsorblock_local import fetch_segments

        return fetch_segments(video_id, categories, **kw)

    def _make_response(self, data: list[dict]) -> MagicMock:
        """Build a fake urllib response context manager."""
        body = json.dumps(data).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    @patch("ytdlp_wrapper.sponsorblock_local.urllib.request.urlopen")
    def test_returns_skip_and_mute_segments(self, mock_urlopen) -> None:
        data = [
            {"segment": [10.0, 30.5], "actionType": "skip", "category": "sponsor"},
            {"segment": [60.0, 70.0], "actionType": "mute", "category": "selfpromo"},
            # "chapter" should be ignored
            {"segment": [5.0, 6.0], "actionType": "chapter", "category": "intro"},
        ]
        mock_urlopen.return_value = self._make_response(data)
        segs = self._fn("abc", ("sponsor", "selfpromo"))
        self.assertEqual(len(segs), 2)
        self.assertIn((10.0, 30.5, "skip"), segs)
        self.assertIn((60.0, 70.0, "mute"), segs)

    @patch("ytdlp_wrapper.sponsorblock_local.urllib.request.urlopen")
    def test_returns_empty_list_on_404(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,  # type: ignore
        )
        segs = self._fn("abc", ("sponsor",))
        self.assertEqual(segs, [])

    @patch("ytdlp_wrapper.sponsorblock_local.urllib.request.urlopen")
    def test_raises_on_500(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="",
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=None,  # type: ignore
        )
        from ytdlp_wrapper.sponsorblock_local import fetch_segments

        with self.assertRaises(urllib.error.HTTPError):
            fetch_segments("abc", ("sponsor",))

    @patch("ytdlp_wrapper.sponsorblock_local.urllib.request.urlopen")
    def test_segments_sorted_by_start_time(self, mock_urlopen) -> None:
        data = [
            {"segment": [90.0, 100.0], "actionType": "skip", "category": "outro"},
            {"segment": [10.0, 20.0], "actionType": "skip", "category": "sponsor"},
        ]
        mock_urlopen.return_value = self._make_response(data)
        segs = self._fn("abc", ("sponsor", "outro"))
        self.assertEqual(segs[0][0], 10.0)
        self.assertEqual(segs[1][0], 90.0)

    @patch("ytdlp_wrapper.sponsorblock_local.urllib.request.urlopen")
    def test_ignores_segments_with_end_before_start(self, mock_urlopen) -> None:
        data = [
            {"segment": [30.0, 10.0], "actionType": "skip", "category": "sponsor"},
            {"segment": [5.0, 15.0], "actionType": "skip", "category": "intro"},
        ]
        mock_urlopen.return_value = self._make_response(data)
        segs = self._fn("abc", ("sponsor", "intro"))
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0], (5.0, 15.0, "skip"))

    @patch("ytdlp_wrapper.sponsorblock_local.urllib.request.urlopen")
    def test_raises_on_network_error(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = OSError("connection refused")
        from ytdlp_wrapper.sponsorblock_local import fetch_segments

        with self.assertRaises(OSError):
            fetch_segments("abc", ("sponsor",))


class TestRemoveSegmentsFfmpeg(unittest.TestCase):
    """Tests for sponsorblock_local.remove_segments_ffmpeg()."""

    def _fn(self, audio_file, segments, ffmpeg_bin="ffmpeg", **kw):
        from ytdlp_wrapper.sponsorblock_local import remove_segments_ffmpeg

        return remove_segments_ffmpeg(audio_file, segments, ffmpeg_bin, **kw)

    def test_noop_when_no_segments(self) -> None:
        """Should not call ffmpeg when the segment list is empty."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / "track.opus"
            audio.write_bytes(b"fake audio")
            with patch("ytdlp_wrapper.sponsorblock_local.subprocess.run") as mock_run:
                self._fn(audio, [])
                mock_run.assert_not_called()
            # File should be untouched.
            self.assertEqual(audio.read_bytes(), b"fake audio")

    @patch("ytdlp_wrapper.sponsorblock_local.subprocess.run")
    def test_calls_ffmpeg_with_aselect_for_skip_segments(self, mock_run) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / "track.opus"
            audio.write_bytes(b"original")

            # Simulate ffmpeg writing to the temp file and exiting 0.
            def fake_run(args, **kw):
                # The temp output path is the last argument.
                tmp_out = Path(args[-1])
                tmp_out.write_bytes(b"processed")
                result = MagicMock()
                result.returncode = 0
                return result

            mock_run.side_effect = fake_run

            self._fn(audio, [(10.0, 30.0, "skip")])

            # ffmpeg must have been called.
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            filter_arg = call_args[call_args.index("-af") + 1]
            self.assertIn("aselect=", filter_arg)
            self.assertIn("between(t,10.0,30.0)", filter_arg)
            self.assertIn("asetpts=N/SR/TB", filter_arg)

            # File should now contain the "processed" bytes.
            self.assertEqual(audio.read_bytes(), b"processed")

    @patch("ytdlp_wrapper.sponsorblock_local.subprocess.run")
    def test_calls_ffmpeg_with_volume_for_mute_segments(self, mock_run) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / "track.opus"
            audio.write_bytes(b"original")

            def fake_run(args, **kw):
                Path(args[-1]).write_bytes(b"muted")
                result = MagicMock()
                result.returncode = 0
                return result

            mock_run.side_effect = fake_run

            self._fn(audio, [(5.0, 10.0, "mute")])

            call_args = mock_run.call_args[0][0]
            filter_arg = call_args[call_args.index("-af") + 1]
            self.assertIn("volume=0", filter_arg)
            self.assertIn("between(t,5.0,10.0)", filter_arg)

    @patch("ytdlp_wrapper.sponsorblock_local.subprocess.run")
    def test_raises_and_cleans_up_on_ffmpeg_failure(self, mock_run) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / "track.opus"
            audio.write_bytes(b"original")

            def fake_run(args, **kw):
                # Write the temp file but return failure.
                Path(args[-1]).write_bytes(b"partial")
                result = MagicMock()
                result.returncode = 1
                result.stderr = "ffmpeg: error"
                return result

            mock_run.side_effect = fake_run

            from ytdlp_wrapper.sponsorblock_local import remove_segments_ffmpeg

            with self.assertRaises(RuntimeError):
                remove_segments_ffmpeg(audio, [(0.0, 5.0, "skip")])

            # Original file should still be intact (temp file cleaned up).
            self.assertEqual(audio.read_bytes(), b"original")
            # No .tmp files left behind.
            tmp_files = list(Path(td).glob("*.tmp*"))
            self.assertEqual(tmp_files, [])


# ---------------------------------------------------------------------------
# Tests for the updated _retry_sponsorblock_for_job()
# ---------------------------------------------------------------------------


class TestRetrySponsorblockForJob(unittest.TestCase):
    """Tests for the local-API-based _retry_sponsorblock_for_job()."""

    def _make_config(
        self, sponsorblock_categories: tuple[str, ...] = ("sponsor",), log_dir=None
    ) -> MagicMock:
        cfg = MagicMock()
        cfg.sponsorblock_categories = sponsorblock_categories
        cfg.sleep_interval = 0
        cfg.ffmpeg_bin = "ffmpeg"
        if log_dir is not None:
            cfg.log_dir = log_dir
        cfg.with_overrides.return_value = cfg
        return cfg

    def _make_job(self, output_dir: Path | None = None) -> MagicMock:
        job = MagicMock()
        job.output_stem = "001-Artist-Song"
        job.source_url = "https://music.youtube.com/watch?v=abc123XYZ"
        job.meta.webpage_url = "https://music.youtube.com/watch?v=abc123XYZ"
        if output_dir is not None:
            job.output_dir = output_dir
        return job

    @patch("ytdlp_wrapper.downloader.remove_segments_ffmpeg")
    @patch("ytdlp_wrapper.downloader.fetch_segments")
    @patch("ytdlp_wrapper.downloader.extract_video_id")
    def test_returns_true_when_segments_found_and_ffmpeg_succeeds(
        self, mock_vid, mock_fetch, mock_ffmpeg
    ) -> None:
        import tempfile
        from ytdlp_wrapper.downloader import _retry_sponsorblock_for_job

        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / "001-Artist-Song.opus"
            audio.touch()
            mock_vid.return_value = "abc123XYZ"
            mock_fetch.return_value = [(10.0, 30.0, "skip")]
            mock_ffmpeg.return_value = None  # success

            cfg = self._make_config(log_dir=Path(td))
            job = self._make_job(output_dir=Path(td))
            result = _retry_sponsorblock_for_job(cfg, job, MagicMock())

        self.assertTrue(result)
        mock_ffmpeg.assert_called_once()

    @patch("ytdlp_wrapper.downloader.fetch_segments")
    @patch("ytdlp_wrapper.downloader.extract_video_id")
    def test_returns_true_and_writes_resolved_marker_when_no_segments(
        self, mock_vid, mock_fetch
    ) -> None:
        import tempfile
        from ytdlp_wrapper.downloader import _retry_sponsorblock_for_job

        with tempfile.TemporaryDirectory() as td:
            mock_vid.return_value = "abc123XYZ"
            mock_fetch.return_value = []  # no segments

            cfg = self._make_config(log_dir=Path(td))
            job = self._make_job(output_dir=Path(td))
            result = _retry_sponsorblock_for_job(cfg, job, MagicMock())

            self.assertTrue(result)
            # Resolved marker must be written to errors.log
            errors_log = Path(td) / "errors.log"
            self.assertTrue(errors_log.exists())
            content = errors_log.read_text(encoding="utf-8")
            self.assertIn("SponsorBlock resolved", content)
            self.assertIn("no segments in database", content)

    @patch("ytdlp_wrapper.downloader.fetch_segments")
    @patch("ytdlp_wrapper.downloader.extract_video_id")
    def test_returns_false_when_api_raises_http_error(
        self, mock_vid, mock_fetch
    ) -> None:
        from ytdlp_wrapper.downloader import _retry_sponsorblock_for_job

        mock_vid.return_value = "abc123XYZ"
        mock_fetch.side_effect = urllib.error.HTTPError(
            url="",
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=None,  # type: ignore
        )
        cfg = self._make_config()
        job = self._make_job()
        result = _retry_sponsorblock_for_job(cfg, job, MagicMock(), attempts=2)
        self.assertFalse(result)

    @patch("ytdlp_wrapper.downloader.fetch_segments")
    @patch("ytdlp_wrapper.downloader.extract_video_id")
    def test_returns_false_when_api_unreachable(self, mock_vid, mock_fetch) -> None:
        from ytdlp_wrapper.downloader import _retry_sponsorblock_for_job

        mock_vid.return_value = "abc123XYZ"
        mock_fetch.side_effect = OSError("connection refused")
        cfg = self._make_config()
        job = self._make_job()
        result = _retry_sponsorblock_for_job(cfg, job, MagicMock(), attempts=1)
        self.assertFalse(result)

    def test_returns_false_when_video_id_cannot_be_extracted(self) -> None:
        from ytdlp_wrapper.downloader import _retry_sponsorblock_for_job

        cfg = self._make_config()
        job = self._make_job()
        job.source_url = ""
        job.meta.webpage_url = ""
        result = _retry_sponsorblock_for_job(cfg, job, MagicMock())
        self.assertFalse(result)

    @patch("ytdlp_wrapper.downloader.remove_segments_ffmpeg")
    @patch("ytdlp_wrapper.downloader.fetch_segments")
    @patch("ytdlp_wrapper.downloader.extract_video_id")
    def test_returns_false_when_ffmpeg_raises(
        self, mock_vid, mock_fetch, mock_ffmpeg
    ) -> None:
        import tempfile
        from ytdlp_wrapper.downloader import _retry_sponsorblock_for_job

        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / "001-Artist-Song.opus"
            audio.touch()
            mock_vid.return_value = "abc123XYZ"
            mock_fetch.return_value = [(10.0, 30.0, "skip")]
            mock_ffmpeg.side_effect = RuntimeError("ffmpeg exited 1")

            cfg = self._make_config(log_dir=Path(td))
            job = self._make_job(output_dir=Path(td))
            result = _retry_sponsorblock_for_job(cfg, job, MagicMock())

        self.assertFalse(result)

    @patch("ytdlp_wrapper.downloader.fetch_segments")
    @patch("ytdlp_wrapper.downloader.extract_video_id")
    def test_removes_pending_task_on_success_no_segments(
        self, mock_vid, mock_fetch
    ) -> None:
        import tempfile
        from ytdlp_wrapper.downloader import _retry_sponsorblock_for_job

        with tempfile.TemporaryDirectory() as td:
            mock_vid.return_value = "abc123XYZ"
            mock_fetch.return_value = []

            cfg = self._make_config(log_dir=Path(td))
            job = self._make_job(output_dir=Path(td))
            pf = MagicMock()
            _retry_sponsorblock_for_job(cfg, job, MagicMock(), pending_file=pf)

        pf.remove_task.assert_called_once()

    @patch("ytdlp_wrapper.downloader.remove_segments_ffmpeg")
    @patch("ytdlp_wrapper.downloader.fetch_segments")
    @patch("ytdlp_wrapper.downloader.extract_video_id")
    def test_removes_pending_task_on_success_with_segments(
        self, mock_vid, mock_fetch, mock_ffmpeg
    ) -> None:
        import tempfile
        from ytdlp_wrapper.downloader import _retry_sponsorblock_for_job

        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / "001-Artist-Song.opus"
            audio.touch()
            mock_vid.return_value = "abc123XYZ"
            mock_fetch.return_value = [(10.0, 30.0, "skip")]
            mock_ffmpeg.return_value = None

            cfg = self._make_config(log_dir=Path(td))
            job = self._make_job(output_dir=Path(td))
            pf = MagicMock()
            _retry_sponsorblock_for_job(cfg, job, MagicMock(), pending_file=pf)

        pf.remove_task.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for _bootstrap_pending_from_logs()
# ---------------------------------------------------------------------------


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

    def test_bootstrap_skips_stems_with_resolved_marker(self) -> None:
        """A stem that has a resolved marker in errors.log must not get a sidecar."""
        import tempfile
        from ytdlp_wrapper.downloader import _bootstrap_pending_from_logs

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = tmp / "003-Artist-Song.opus"
            audio.touch()
            self._write_errors_log(
                tmp,
                [
                    # First: an API failure that would normally create a sidecar.
                    "2026-02-19T15:06:05 003-Artist-Song"
                    " | exit 1 | ERROR: Preprocessing: Unable to communicate with"
                    " SponsorBlock API: HTTP Error 500"
                    " | https://music.youtube.com/watch?v=xyz",
                    # Then: later confirmed clean (resolved marker).
                    "2026-02-19T16:00:00 003-Artist-Song"
                    " | SponsorBlock resolved — no segments in database",
                ],
            )
            cfg = self._make_config(tmp)
            created = _bootstrap_pending_from_logs(cfg, MagicMock())
            # Resolved marker must prevent sidecar creation.
            self.assertEqual(created, 0)
            sidecar = tmp / "003-Artist-Song.pending.json"
            self.assertFalse(sidecar.exists())

    def test_bootstrap_resolved_marker_does_not_affect_other_stems(self) -> None:
        """Resolved marker for stem A must not suppress sidecar creation for stem B."""
        import tempfile
        from ytdlp_wrapper.downloader import _bootstrap_pending_from_logs

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio_a = tmp / "001-Stem-A.opus"
            audio_b = tmp / "002-Stem-B.opus"
            audio_a.touch()
            audio_b.touch()
            self._write_errors_log(
                tmp,
                [
                    # Stem A: failed then resolved.
                    "2026-02-19T15:06:05 001-Stem-A"
                    " | exit 1 | ERROR: Preprocessing: Unable to communicate with"
                    " SponsorBlock API: HTTP Error 500"
                    " | https://music.youtube.com/watch?v=aaa",
                    "2026-02-19T16:00:00 001-Stem-A"
                    " | SponsorBlock resolved — no segments in database",
                    # Stem B: only failure (no resolved marker).
                    "2026-02-19T15:07:00 002-Stem-B"
                    " | exit 1 | ERROR: Preprocessing: Unable to communicate with"
                    " SponsorBlock API: HTTP Error 500"
                    " | https://music.youtube.com/watch?v=bbb",
                ],
            )
            cfg = self._make_config(tmp)
            created = _bootstrap_pending_from_logs(cfg, MagicMock())
            self.assertEqual(created, 1)
            self.assertFalse((tmp / "001-Stem-A.pending.json").exists())
            self.assertTrue((tmp / "002-Stem-B.pending.json").exists())


# ---------------------------------------------------------------------------
# End-to-end integration tests for process_pending_sponsorblock
# ---------------------------------------------------------------------------


class TestProcessPendingSponsorblockIntegration(unittest.TestCase):
    """Integration tests that exercise the full retry cycle end-to-end.

    These tests run ``process_pending_sponsorblock`` against real files on
    disk (inside a temp directory) with only the network calls mocked out,
    so they verify that sidecars are actually created, processed, and deleted
    — not just that the right methods are called.
    """

    def _make_config(self, tmp: Path) -> MagicMock:
        log_dir = tmp / "logs"
        log_dir.mkdir()
        cfg = MagicMock()
        cfg.log_dir = log_dir
        cfg.base_dir = tmp
        cfg.sponsorblock_categories = ("sponsor",)
        cfg.sleep_interval = 0
        cfg.ffmpeg_bin = "ffmpeg"
        return cfg

    def test_no_segments_deletes_sidecar_and_writes_resolved_marker(self) -> None:
        """When SponsorBlock returns no segments the sidecar must be deleted and
        a resolved marker written so the stem is not re-queued on the next run."""
        import tempfile

        from ytdlp_wrapper.downloader import process_pending_sponsorblock
        from ytdlp_wrapper.pending import (
            PENDING_TASK_SPONSORBLOCK,
            find_pending_sidecars,
        )

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = tmp / "048-Artist-Song.opus"
            audio.touch()
            cfg = self._make_config(tmp)

            # Seed errors.log with a SponsorBlock API failure so bootstrap creates
            # a sidecar on the first run.
            (cfg.log_dir / "errors.log").write_text(
                "2026-02-19T15:06:05 048-Artist-Song"
                " | exit 1 | ERROR: Preprocessing: Unable to communicate with"
                " SponsorBlock API: HTTP Error 500"
                " | https://music.youtube.com/watch?v=vid001\n",
                encoding="utf-8",
            )
            (cfg.log_dir / "success.log").write_text(
                f"2026-02-19T12:00:00 048-Artist-Song | {tmp}"
                " | https://music.youtube.com/watch?v=vid001\n",
                encoding="utf-8",
            )

            logger = MagicMock()

            # First run: API returns no segments → sidecar should be deleted,
            # resolved marker written.
            with (
                patch(
                    "ytdlp_wrapper.downloader.extract_video_id",
                    return_value="vid001",
                ),
                patch(
                    "ytdlp_wrapper.downloader.fetch_segments",
                    return_value=[],
                ),
            ):
                process_pending_sponsorblock(cfg, logger)

            sidecar = tmp / "048-Artist-Song.pending.json"
            self.assertFalse(
                sidecar.exists(), "Sidecar must be deleted after no-segments run"
            )

            errors_text = (cfg.log_dir / "errors.log").read_text(encoding="utf-8")
            self.assertIn(
                "SponsorBlock resolved \u2014 no segments in database",
                errors_text,
                "Resolved marker must be written to errors.log",
            )

            # Second run: bootstrap must see the resolved marker and not re-create
            # the sidecar; find_pending_sidecars must return nothing.
            with (
                patch(
                    "ytdlp_wrapper.downloader.extract_video_id",
                    return_value="vid001",
                ),
                patch(
                    "ytdlp_wrapper.downloader.fetch_segments",
                    return_value=[],
                ),
            ):
                process_pending_sponsorblock(cfg, logger)

            self.assertFalse(
                sidecar.exists(), "Sidecar must not be re-created on second run"
            )
            pending = find_pending_sidecars(tmp, task=PENDING_TASK_SPONSORBLOCK)
            self.assertEqual(
                len(pending),
                0,
                "No pending sidecars must remain after second run",
            )

    def test_segments_found_runs_ffmpeg_and_deletes_sidecar(self) -> None:
        """When SponsorBlock returns segments, ffmpeg must be called and the
        sidecar deleted on success."""
        import tempfile

        from ytdlp_wrapper.downloader import process_pending_sponsorblock
        from ytdlp_wrapper.pending import (
            PENDING_TASK_SPONSORBLOCK,
            find_pending_sidecars,
        )

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = tmp / "001-Artist-Track.opus"
            audio.touch()
            cfg = self._make_config(tmp)

            (cfg.log_dir / "errors.log").write_text(
                "2026-02-19T15:06:05 001-Artist-Track"
                " | exit 1 | ERROR: Preprocessing: Unable to communicate with"
                " SponsorBlock API: HTTP Error 500"
                " | https://music.youtube.com/watch?v=vid002\n",
                encoding="utf-8",
            )
            (cfg.log_dir / "success.log").write_text(
                f"2026-02-19T12:00:00 001-Artist-Track | {tmp}"
                " | https://music.youtube.com/watch?v=vid002\n",
                encoding="utf-8",
            )

            logger = MagicMock()

            with (
                patch(
                    "ytdlp_wrapper.downloader.extract_video_id",
                    return_value="vid002",
                ),
                patch(
                    "ytdlp_wrapper.downloader.fetch_segments",
                    return_value=[(10.0, 30.0, "skip")],
                ),
                patch("ytdlp_wrapper.downloader.remove_segments_ffmpeg"),
            ):
                process_pending_sponsorblock(cfg, logger)

            sidecar = tmp / "001-Artist-Track.pending.json"
            self.assertFalse(
                sidecar.exists(), "Sidecar must be deleted after ffmpeg succeeds"
            )
            pending = find_pending_sidecars(tmp, task=PENDING_TASK_SPONSORBLOCK)
            self.assertEqual(len(pending), 0, "No pending sidecars must remain")

    def test_transient_api_failure_keeps_sidecar(self) -> None:
        """When the SponsorBlock API is unreachable (all attempts fail) the
        sidecar must remain on disk for the next retry run."""
        import tempfile

        from ytdlp_wrapper.downloader import process_pending_sponsorblock
        from ytdlp_wrapper.pending import (
            PENDING_TASK_SPONSORBLOCK,
            find_pending_sidecars,
        )

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = tmp / "002-Artist-Track.opus"
            audio.touch()
            cfg = self._make_config(tmp)

            (cfg.log_dir / "errors.log").write_text(
                "2026-02-19T15:06:05 002-Artist-Track"
                " | exit 1 | ERROR: Preprocessing: Unable to communicate with"
                " SponsorBlock API: HTTP Error 500"
                " | https://music.youtube.com/watch?v=vid003\n",
                encoding="utf-8",
            )
            (cfg.log_dir / "success.log").write_text(
                f"2026-02-19T12:00:00 002-Artist-Track | {tmp}"
                " | https://music.youtube.com/watch?v=vid003\n",
                encoding="utf-8",
            )

            logger = MagicMock()

            with (
                patch(
                    "ytdlp_wrapper.downloader.extract_video_id",
                    return_value="vid003",
                ),
                patch(
                    "ytdlp_wrapper.downloader.fetch_segments",
                    side_effect=OSError("connection refused"),
                ),
            ):
                process_pending_sponsorblock(cfg, logger)

            sidecar = tmp / "002-Artist-Track.pending.json"
            self.assertTrue(
                sidecar.exists(),
                "Sidecar must be kept when API is unreachable",
            )
            pending = find_pending_sidecars(tmp, task=PENDING_TASK_SPONSORBLOCK)
            self.assertEqual(len(pending), 1, "Sidecar must still be pending")


if __name__ == "__main__":
    unittest.main()
