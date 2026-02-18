"""Command-line interface for yt-dlp wrapper."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import Config
from .downloader import (
    DownloadError,
    copy_cookies,
    download_url,
    ensure_dependencies,
    ensure_log_dirs,
    load_sponsorblock_categories,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="YouTube Music downloader using yt-dlp"
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="YouTube Music URL (playlist or single video). If omitted, you will be prompted.",
    )
    parser.add_argument(
        "--url",
        dest="url_flag",
        help="YouTube Music URL (playlist or single video).",
    )
    parser.add_argument(
        "--cookies", help="Path to cookies.txt to copy into yt-dlp config"
    )
    parser.add_argument(
        "--base-dir", default="/media/music", help="Base download directory"
    )
    parser.add_argument("--log-dir", default="/media/music/.logs", help="Log directory")
    parser.add_argument(
        "--download-archive",
        default="/media/music/.logs/download_archive.txt",
        help="yt-dlp download archive file",
    )
    parser.add_argument(
        "--metadata-cache-dir",
        default="~/.cache/ytdownloader/metadata",
        help="Directory for cached yt-dlp metadata",
    )
    parser.add_argument(
        "--metadata-cache-ttl-days",
        type=int,
        default=30,
        help="Days to keep cached metadata before refresh (default: 30)",
    )
    parser.add_argument(
        "--disable-metadata-cache",
        action="store_true",
        help="Disable metadata cache reads/writes",
    )
    parser.add_argument(
        "--purge-metadata-cache",
        action="store_true",
        help="Delete all cached metadata entries before running",
    )
    parser.add_argument("--sleep-interval", type=int, default=1)
    parser.add_argument("--max-sleep-interval", type=int, default=3)
    parser.add_argument(
        "--sleep-requests",
        type=int,
        default=2,
        help="Seconds to sleep between metadata requests to avoid rate limits",
    )
    parser.add_argument(
        "--rate-limit", default="2M", help="Rate limit (e.g. 2M). Use 0 to disable"
    )
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--audio-format",
        default="opus",
        help="Target audio format for Navidrome compatibility (default: opus)",
    )
    parser.add_argument(
        "--rewrite-m3u",
        help="Rewrite playlist M3U from existing files in the given directory",
    )
    parser.add_argument(
        "--rewrite-m3u-all",
        action="store_true",
        help="Rewrite all playlist M3U files under the base directory",
    )
    parser.add_argument(
        "--playlist-url",
        metavar="URL",
        help=(
            "Playlist URL to store as a #PLAYLIST-URL: comment when used with "
            "--rewrite-m3u. Has no effect without --rewrite-m3u."
        ),
    )
    parser.add_argument(
        "--retag",
        metavar="DIRECTORY",
        help="Retroactively fix Navidrome compilation tags on all audio files in DIRECTORY.",
    )
    parser.add_argument(
        "--retag-all",
        action="store_true",
        help="Like --retag but runs on every subdirectory of the base directory.",
    )
    parser.add_argument(
        "--reprocess-playlists",
        action="store_true",
        help=(
            "Re-download all playlists whose M3U files contain a stored #PLAYLIST-URL: "
            "comment, applying SponsorBlock trimming and refreshing the audio files. "
            "Downloads go to a temp directory first and are atomically swapped in only "
            "if successful, preserving originals on failure."
        ),
    )
    parser.add_argument(
        "--stamp-missing-urls",
        action="store_true",
        help=(
            "Scan all playlist folders under the base directory, find any whose M3U "
            "file is missing a #PLAYLIST-URL: comment, and interactively prompt you "
            "to supply the URL for each one. Skips playlists that already have a URL "
            "stored. Run this once before --reprocess-playlists."
        ),
    )
    return parser


def configure_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ytdlp_wrapper.log"
    logger = logging.getLogger("ytdlp_wrapper")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)
    # Stream handler: plain stdout for non-download commands.
    # During a download session ProgressReporter will replace this with a
    # RichHandler so all output is routed through the same Rich console,
    # preventing interleaving with the progress bar.
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.set_name("stream")
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    url = args.url_flag or args.url
    _offline_mode = (
        args.rewrite_m3u
        or args.rewrite_m3u_all
        or getattr(args, "retag", None)
        or getattr(args, "retag_all", False)
        or getattr(args, "reprocess_playlists", False)
        or getattr(args, "stamp_missing_urls", False)
    )
    if _offline_mode:
        url = ""
    if not url and args.purge_metadata_cache:
        url = ""
    if not url and not args.purge_metadata_cache and not _offline_mode:
        try:
            url = input("Paste YouTube Music URL: ").strip()
        except EOFError:
            url = ""
    if not url and not args.purge_metadata_cache and not _offline_mode:
        parser.error("url is required")

    config = Config().with_overrides(
        base_dir=args.base_dir,
        log_dir=args.log_dir,
        download_archive=args.download_archive,
        metadata_cache_dir=args.metadata_cache_dir,
        metadata_cache_ttl_days=args.metadata_cache_ttl_days,
        metadata_cache_enabled=not args.disable_metadata_cache,
        sleep_interval=args.sleep_interval,
        max_sleep_interval=args.max_sleep_interval,
        sleep_requests=args.sleep_requests,
        rate_limit=None if str(args.rate_limit) == "0" else args.rate_limit,
        concurrent_downloads=args.concurrency,
        retries=args.retries,
        audio_format=args.audio_format,
    )
    logger = configure_logging(config.log_dir)

    # Load SponsorBlock categories from sponsorblock.txt in the project root.
    sb_categories = load_sponsorblock_categories(config.sponsorblock_config, logger)
    config = config.with_overrides(sponsorblock_categories=sb_categories)

    try:
        ensure_dependencies(config)
        ensure_log_dirs(config)
        if args.cookies:
            copy_cookies(config, args.cookies, logger)
        if config.cookies_path.exists():
            logger.info("Using cookies from %s", config.cookies_path)
        if args.purge_metadata_cache:
            from .metadata_cache import purge_metadata_cache

            purged = purge_metadata_cache(config, logger)
            logger.info("Purged %s metadata cache entries", purged)
            if not url:
                return 0
        if args.rewrite_m3u:
            from .downloader import rewrite_m3u_from_dir

            rewrite_m3u_from_dir(
                Path(args.rewrite_m3u).expanduser(),
                config,
                logger,
                playlist_url=getattr(args, "playlist_url", None),
            )
            return 0
        if args.rewrite_m3u_all:
            from .downloader import rewrite_all_m3u

            rewrite_all_m3u(config, logger)
            return 0
        if getattr(args, "retag", None):
            from .downloader import retag_playlist_dir

            updated = retag_playlist_dir(Path(args.retag).expanduser(), config, logger)
            logger.info("Done. %s file(s) retagged.", updated)
            return 0
        if getattr(args, "retag_all", False):
            from .downloader import retag_all_playlist_dirs

            retag_all_playlist_dirs(config, logger)
            return 0
        if getattr(args, "reprocess_playlists", False):
            from .downloader import reprocess_all_playlists

            reprocess_all_playlists(config, logger)
            return 0
        if getattr(args, "stamp_missing_urls", False):
            from .downloader import stamp_missing_playlist_urls

            stamp_missing_playlist_urls(config, logger)
            return 0
        download_url(config, url, logger)
    except DownloadError as exc:
        logger.error("Error: %s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        logger.exception("Unhandled error: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
