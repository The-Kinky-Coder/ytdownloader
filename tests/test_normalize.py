import tempfile
import time
import unittest
from pathlib import Path
from typing import Iterable

from ytdlp_wrapper import normalize


class DummyAudio:
    def __init__(self):
        self.tags = {}

    def save(self, *args, **kwargs):
        pass


class TestTagHelpers(unittest.TestCase):
    def setUp(self):
        # patch MutagenFile to return a persistent DummyAudio per path
        self._orig = normalize.MutagenFile
        self._map: dict[str, DummyAudio] = {}
        def fake_mutagen(path, easy=False):
            key = str(path)
            if key not in self._map:
                self._map[key] = DummyAudio()
            return self._map[key]
        normalize.MutagenFile = fake_mutagen

    def tearDown(self):
        normalize.MutagenFile = self._orig

    def test_mark_and_check(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()
        p = Path(tmp.name)
        self.assertFalse(normalize.is_normalized(p))
        self.assertTrue(normalize.mark_normalized(p))
        self.assertTrue(normalize.is_normalized(p))

    def test_is_normalized_handles_missing(self):
        # if MutagenFile returns None, should not raise
        normalize.MutagenFile = lambda path, easy=False: None
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()
        p = Path(tmp.name)
        self.assertFalse(normalize.is_normalized(p))


class TestNormalizeCalls(unittest.TestCase):
    def setUp(self):
        # save originals so we can restore after each test
        self._orig_is = normalize.is_normalized
        self._orig_nf = normalize.normalize_file
        self._orig_nfs = normalize.normalize_files

    def tearDown(self):
        normalize.is_normalized = self._orig_is
        normalize.normalize_file = self._orig_nf
        normalize.normalize_files = self._orig_nfs

    def test_unsupported_extension(self):
        p = Path("/tmp/file.txt")
        self.assertFalse(normalize.normalize_file(p))

    def test_skip_if_already_normalized(self):
        p = Path("/tmp/sunny.opus")
        # monkeypatch is_normalized to True and ensure measure_loudness not called
        normalize.is_normalized = lambda path: True
        called = False
        normalize.measure_loudness = lambda path: (_ for _ in ()).throw(AssertionError("should not be called"))  # type: ignore
        self.assertTrue(normalize.normalize_file(p))

    def test_normalize_files_skips_tagged(self):
        files = [Path("a.mp3"), Path("skip.mp3")]
        # patch normalize_file and is_normalized
        called: list[Path] = []
        def fake_norm(p: Path) -> bool:
            called.append(p)
            return True
        normalize.normalize_file = fake_norm
        normalize.is_normalized = lambda p: p.name == "skip.mp3"
        success, failed = normalize.normalize_files(files, workers=1, logger=None)
        self.assertEqual(success, 1)
        self.assertEqual(failed, 0)
        self.assertEqual(called, [Path("a.mp3")])

    def test_progress_reporting(self):
        # two files, both succeed; progress object should see add/complete calls
        files = [Path("one.mp3"), Path("two.mp3")]
        normalize.normalize_file = lambda p: True
        # dummy progress that records calls
        calls: list = []
        class DummyProg:
            def add_task(self, key, label):
                calls.append(("add", key, label))
            def complete(self, key):
                calls.append(("complete", key))
        prog = DummyProg()
        success, failed = normalize.normalize_files(
            files, workers=1, logger=None, progress=prog
        )
        self.assertEqual((success, failed), (2, 0))
        expected = [
            ("add", "one.mp3", "one.mp3"),
            ("add", "two.mp3", "two.mp3"),
            ("complete", "one.mp3"),
            ("complete", "two.mp3"),
        ]
        self.assertEqual(calls, expected)


if __name__ == "__main__":
    unittest.main()
