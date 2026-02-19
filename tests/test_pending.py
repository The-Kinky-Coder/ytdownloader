"""Tests for the pending sidecar system (pending.py)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
import tempfile

from ytdlp_wrapper.pending import (
    PENDING_TASK_SPONSORBLOCK,
    PendingFile,
    audio_file_to_sidecar,
    find_pending_sidecars,
    sidecar_path_for_stem,
    write_pending,
)


class TestAudioFileToSidecar(unittest.TestCase):
    def test_replaces_audio_extension(self) -> None:
        p = Path("/music/Playlist/001-Artist-Song.opus")
        self.assertEqual(
            audio_file_to_sidecar(p),
            Path("/music/Playlist/001-Artist-Song.pending.json"),
        )

    def test_works_with_m4a(self) -> None:
        p = Path("/music/001-Song.m4a")
        self.assertEqual(audio_file_to_sidecar(p), Path("/music/001-Song.pending.json"))

    def test_sidecar_path_for_stem(self) -> None:
        d = Path("/music/Playlist")
        self.assertEqual(
            sidecar_path_for_stem(d, "001-Artist-Song"),
            Path("/music/Playlist/001-Artist-Song.pending.json"),
        )


class TestPendingFileRoundtrip(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_audio(self, name: str = "001-Artist-Song.opus") -> Path:
        p = self.tmp / name
        p.write_text("fake audio", encoding="utf-8")
        return p

    # ------------------------------------------------------------------
    # write_pending / PendingFile.save / PendingFile.delete
    # ------------------------------------------------------------------

    def test_write_creates_sidecar(self) -> None:
        audio = self._make_audio()
        pf = write_pending(
            audio, "https://example.com/v=1", "001-Artist-Song", ["sponsorblock"]
        )
        sidecar = audio_file_to_sidecar(audio)
        self.assertTrue(sidecar.exists())
        data = json.loads(sidecar.read_text())
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["source_url"], "https://example.com/v=1")
        self.assertEqual(data["output_stem"], "001-Artist-Song")
        self.assertIn("sponsorblock", data["pending"])

    def test_write_merges_tasks_into_existing_sidecar(self) -> None:
        audio = self._make_audio()
        write_pending(
            audio, "https://example.com/v=1", "001-Artist-Song", ["sponsorblock"]
        )
        write_pending(
            audio, "https://example.com/v=1", "001-Artist-Song", ["future_task"]
        )
        sidecar = audio_file_to_sidecar(audio)
        data = json.loads(sidecar.read_text())
        self.assertIn("sponsorblock", data["pending"])
        self.assertIn("future_task", data["pending"])

    def test_write_no_duplicates_on_repeat_call(self) -> None:
        audio = self._make_audio()
        write_pending(
            audio, "https://example.com/v=1", "001-Artist-Song", ["sponsorblock"]
        )
        write_pending(
            audio, "https://example.com/v=1", "001-Artist-Song", ["sponsorblock"]
        )
        sidecar = audio_file_to_sidecar(audio)
        data = json.loads(sidecar.read_text())
        self.assertEqual(data["pending"].count("sponsorblock"), 1)

    def test_delete_removes_sidecar(self) -> None:
        audio = self._make_audio()
        pf = write_pending(
            audio, "https://example.com/v=1", "001-Artist-Song", ["sponsorblock"]
        )
        sidecar = audio_file_to_sidecar(audio)
        self.assertTrue(sidecar.exists())
        pf.delete()
        self.assertFalse(sidecar.exists())

    def test_delete_is_idempotent(self) -> None:
        audio = self._make_audio()
        pf = write_pending(
            audio, "https://example.com/v=1", "001-Artist-Song", ["sponsorblock"]
        )
        pf.delete()
        pf.delete()  # second call should not raise

    # ------------------------------------------------------------------
    # remove_task
    # ------------------------------------------------------------------

    def test_remove_task_deletes_sidecar_when_last_task(self) -> None:
        audio = self._make_audio()
        pf = write_pending(
            audio, "https://example.com/v=1", "001-Artist-Song", ["sponsorblock"]
        )
        pf.remove_task("sponsorblock")
        self.assertFalse(audio_file_to_sidecar(audio).exists())

    def test_remove_task_keeps_sidecar_when_other_tasks_remain(self) -> None:
        audio = self._make_audio()
        pf = write_pending(
            audio,
            "https://example.com/v=1",
            "001-Artist-Song",
            ["sponsorblock", "future"],
        )
        pf.remove_task("sponsorblock")
        sidecar = audio_file_to_sidecar(audio)
        self.assertTrue(sidecar.exists())
        data = json.loads(sidecar.read_text())
        self.assertNotIn("sponsorblock", data["pending"])
        self.assertIn("future", data["pending"])

    def test_remove_nonexistent_task_is_safe(self) -> None:
        audio = self._make_audio()
        pf = write_pending(
            audio, "https://example.com/v=1", "001-Artist-Song", ["sponsorblock"]
        )
        pf.remove_task(
            "nonexistent"
        )  # should not raise; sidecar still has sponsorblock
        sidecar = audio_file_to_sidecar(audio)
        self.assertTrue(sidecar.exists())

    def test_has_task(self) -> None:
        audio = self._make_audio()
        pf = write_pending(
            audio, "https://example.com/v=1", "001-Artist-Song", ["sponsorblock"]
        )
        self.assertTrue(pf.has_task(PENDING_TASK_SPONSORBLOCK))
        self.assertFalse(pf.has_task("other"))

    # ------------------------------------------------------------------
    # find_pending_sidecars
    # ------------------------------------------------------------------

    def test_find_returns_sidecar_for_matching_task(self) -> None:
        audio = self._make_audio()
        write_pending(
            audio, "https://example.com/v=1", "001-Artist-Song", ["sponsorblock"]
        )
        found = find_pending_sidecars(self.tmp, task="sponsorblock")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].output_stem, "001-Artist-Song")

    def test_find_returns_all_when_no_task_filter(self) -> None:
        audio1 = self._make_audio("001-A.opus")
        audio2 = self._make_audio("002-B.opus")
        write_pending(audio1, "https://example.com/v=1", "001-A", ["sponsorblock"])
        write_pending(audio2, "https://example.com/v=2", "002-B", ["future_task"])
        found = find_pending_sidecars(self.tmp)
        self.assertEqual(len(found), 2)

    def test_find_filters_by_task(self) -> None:
        audio1 = self._make_audio("001-A.opus")
        audio2 = self._make_audio("002-B.opus")
        write_pending(audio1, "https://example.com/v=1", "001-A", ["sponsorblock"])
        write_pending(audio2, "https://example.com/v=2", "002-B", ["other_task"])
        found = find_pending_sidecars(self.tmp, task="sponsorblock")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].output_stem, "001-A")

    def test_find_skips_sidecar_with_no_matching_audio(self) -> None:
        # Write a sidecar manually with no corresponding audio file.
        orphan = self.tmp / "ghost.pending.json"
        orphan.write_text(
            json.dumps(
                {
                    "version": 1,
                    "source_url": "",
                    "output_stem": "ghost",
                    "pending": ["sponsorblock"],
                    "created": "2026-01-01T00:00:00",
                }
            ),
            encoding="utf-8",
        )
        found = find_pending_sidecars(self.tmp, task="sponsorblock")
        self.assertEqual(len(found), 0)

    def test_find_skips_corrupt_sidecar(self) -> None:
        audio = self._make_audio()
        sidecar = audio_file_to_sidecar(audio)
        sidecar.write_text("this is not json", encoding="utf-8")
        found = find_pending_sidecars(self.tmp, task="sponsorblock")
        self.assertEqual(len(found), 0)

    def test_find_ignores_temp_audio_artifacts(self) -> None:
        # Regression: yt-dlp leaves behind zero-byte .temp.opus files alongside
        # the real audio file.  If find_pending_sidecars picked the .temp. file
        # as the audio_file, sidecar_path would compute to foo.temp.pending.json
        # (non-existent), causing delete() to silently no-op and the real sidecar
        # to never be removed.
        audio = self._make_audio("048-Artist-Song.opus")
        temp_artifact = self.tmp / "048-Artist-Song.temp.opus"
        temp_artifact.touch()
        pf = write_pending(
            audio, "https://example.com/v=1", "048-Artist-Song", ["sponsorblock"]
        )
        found = find_pending_sidecars(self.tmp, task="sponsorblock")
        self.assertEqual(len(found), 1)
        # The audio_file must point to the real .opus, not the .temp.opus artifact.
        self.assertEqual(found[0].audio_file.name, "048-Artist-Song.opus")
        # Crucially: remove_task must delete the real sidecar, not a phantom path.
        found[0].remove_task("sponsorblock")
        self.assertFalse(audio_file_to_sidecar(audio).exists())

    def test_find_searches_subdirectories(self) -> None:
        subdir = self.tmp / "Playlist"
        subdir.mkdir()
        audio = subdir / "001-Artist-Song.opus"
        audio.write_text("fake", encoding="utf-8")
        write_pending(
            audio, "https://example.com/v=1", "001-Artist-Song", ["sponsorblock"]
        )
        found = find_pending_sidecars(self.tmp, task="sponsorblock")
        self.assertEqual(len(found), 1)


if __name__ == "__main__":
    unittest.main()
