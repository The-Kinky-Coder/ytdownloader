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

    def test_no_compilation_flag(self):
        args = self.parser.parse_args(["--no-compilation"])
        # flag should be present and True; inversion logic handled in cli.main
        self.assertTrue(args.no_compilation)
        # simulate inversion as done by main
        playlist_compilation = not args.no_compilation
        self.assertFalse(playlist_compilation)

    def test_default_sponsorblock_categories(self):
        # mimic the logic from cli.main with no config file present
        user_cfg: dict = {}
        _sb_raw = user_cfg.get("sponsorblock_categories")
        if _sb_raw is None:
            cats = Config().sponsorblock_categories
        else:
            cats = tuple(c.strip() for c in _sb_raw.split(",") if c.strip())
        self.assertEqual(cats, ("sponsor", "selfpromo", "interaction"))
        # blank string should disable
        user_cfg = {"sponsorblock_categories": ""}
        _sb_raw = user_cfg.get("sponsorblock_categories")
        if _sb_raw is None:
            cats2 = Config().sponsorblock_categories
        else:
            cats2 = tuple(c.strip() for c in _sb_raw.split(",") if c.strip())
        self.assertEqual(cats2, ())


if __name__ == "__main__":
    unittest.main()
