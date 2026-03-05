import unittest
from unittest.mock import patch, MagicMock

from ytdlp_wrapper.cli import build_parser
from ytdlp_wrapper.config import Config


class TestCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()
        # guard against unexpected prompts – tests should never block on input
        # and verify the CLI never tries to read from stdin.
        self._input_patcher = patch("builtins.input", autospec=True)
        self.input_mock = self._input_patcher.start()

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

    def test_retry_thumbnails_flag(self):
        args = self.parser.parse_args(["--retry-thumbnails"])
        self.assertTrue(args.retry_thumbnails)

    def test_generate_thumbnails_flag(self):
        # with directory
        args = self.parser.parse_args(["--generate-thumbnails", "foo"])
        self.assertEqual(args.generate_thumbnails, "foo")
        # without directory
        args = self.parser.parse_args(["--generate-thumbnails"])
        self.assertIsNone(args.generate_thumbnails)

    def tearDown(self) -> None:
        # restore any patched globals from setUp
        self._input_patcher.stop()

    def test_generate_thumbnails_invokes_function(self):
        args = ["--generate-thumbnails"]
        called = []
        def fake_gen(cfg, logger, directory=None):
            called.append(directory)
        from ytdlp_wrapper import downloader
        # patch the function but restore it afterwards so other tests aren't affected
        orig = downloader.generate_thumbnails
        try:
            downloader.generate_thumbnails = fake_gen
            self.parser.parse_args(args)
            # run via main to ensure it actually calls; input stubbed so test won't hang
            from ytdlp_wrapper.cli import main
            main(args)
        finally:
            downloader.generate_thumbnails = orig
        self.assertEqual(called, [None])
        # ensure no prompt was attempted
        self.input_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
