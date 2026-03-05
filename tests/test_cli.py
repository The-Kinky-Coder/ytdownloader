import unittest

from ytdlp_wrapper.cli import build_parser
from ytdlp_wrapper.config import Config


class TestCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_no_normalize_flag(self):
        args = self.parser.parse_args(["--no-normalize"])
        config = Config().with_overrides(normalize=not args.no_normalize)
        self.assertFalse(config.normalize)

    def test_normalize_workers_and_lufs(self):
        args = self.parser.parse_args([
            "--normalize-workers",
            "4",
            "--normalize-lufs",
            "-16.5",
        ])
        config = Config().with_overrides(
            normalize_workers=args.normalize_workers,
            normalize_lufs=args.normalize_lufs,
        )
        self.assertEqual(config.normalize_workers, 4)
        self.assertEqual(config.normalize_lufs, -16.5)

    def test_background_flag_affects_config(self):
        args = self.parser.parse_args(["--normalize-background"])
        config = Config().with_overrides(normalize_background=args.normalize_background)
        self.assertTrue(config.normalize_background)


if __name__ == "__main__":
    unittest.main()
