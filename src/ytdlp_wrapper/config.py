"""Configuration defaults and helpers."""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

USER_CONFIG_PATH = Path("~/.config/ytdlp-wrapper/config.ini").expanduser()
USER_CONFIG_DIR = USER_CONFIG_PATH.parent

# Cookies can live in the ytdlp-wrapper config dir as a convenience.
# If found there it is copied to the yt-dlp standard location on startup.
WRAPPER_COOKIES_PATH = USER_CONFIG_DIR / "cookies.txt"
YTDLP_COOKIES_PATH = Path("~/.config/yt-dlp/cookies.txt").expanduser()


def load_user_config(config_path: Path = USER_CONFIG_PATH) -> dict:
    """Read ~/.config/ytdlp-wrapper/config.ini and return overrides as a dict.

    Only keys that are explicitly set in the file are returned — missing keys
    are omitted so callers can distinguish "not set" from "set to default".

    Supported keys (all in [ytdlp-wrapper] section):
        base_dir                 = /media/music
        log_dir                  = /media/music/.logs
        download_archive         = /media/music/.logs/download_archive.txt
        sponsorblock_categories  = sponsor,selfpromo,interaction
    """
    if not config_path.exists():
        return {}
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    section = "ytdlp-wrapper"
    if not parser.has_section(section):
        return {}
    return dict(parser[section])


@dataclass(frozen=True)
class Config:
    base_dir: Path = Path("/media/music")
    log_dir: Path = Path("/media/music/.logs")
    cookies_path: Path = YTDLP_COOKIES_PATH
    download_archive: Path = Path("/media/music/.logs/download_archive.txt")
    metadata_cache_dir: Path = Path("~/.cache/ytdownloader/metadata").expanduser()
    metadata_cache_ttl_days: int = 30
    metadata_cache_enabled: bool = True
    sleep_interval: int = 1
    max_sleep_interval: int = 3
    sleep_requests: int = 2
    rate_limit: str | None = "2M"
    concurrent_downloads: int = 5
    retries: int = 3
    audio_format: str = "opus"
    yt_dlp_bin: str = "yt-dlp"
    ffmpeg_bin: str = "ffmpeg"
    # Tuple of SponsorBlock category strings to remove.
    # Empty tuple = SponsorBlock disabled (no --sponsorblock-remove flag passed).
    # Loaded from config.ini [ytdlp-wrapper] sponsorblock_categories key.
    sponsorblock_categories: tuple[str, ...] = field(default_factory=tuple)

    def with_overrides(
        self,
        *,
        base_dir: str | None = None,
        log_dir: str | None = None,
        cookies_path: str | None = None,
        download_archive: str | None = None,
        metadata_cache_dir: str | None = None,
        metadata_cache_ttl_days: int | None = None,
        metadata_cache_enabled: bool | None = None,
        sleep_interval: int | None = None,
        max_sleep_interval: int | None = None,
        sleep_requests: int | None = None,
        rate_limit: str | None = None,
        concurrent_downloads: int | None = None,
        retries: int | None = None,
        audio_format: str | None = None,
        sponsorblock_categories: tuple[str, ...] | None = None,
    ) -> "Config":
        return Config(
            base_dir=Path(base_dir) if base_dir else self.base_dir,
            log_dir=Path(log_dir) if log_dir else self.log_dir,
            cookies_path=Path(cookies_path).expanduser()
            if cookies_path
            else self.cookies_path,
            download_archive=Path(download_archive)
            if download_archive
            else self.download_archive,
            metadata_cache_dir=Path(metadata_cache_dir).expanduser()
            if metadata_cache_dir
            else self.metadata_cache_dir,
            metadata_cache_ttl_days=metadata_cache_ttl_days
            if metadata_cache_ttl_days is not None
            else self.metadata_cache_ttl_days,
            metadata_cache_enabled=metadata_cache_enabled
            if metadata_cache_enabled is not None
            else self.metadata_cache_enabled,
            sleep_interval=sleep_interval
            if sleep_interval is not None
            else self.sleep_interval,
            max_sleep_interval=max_sleep_interval
            if max_sleep_interval is not None
            else self.max_sleep_interval,
            sleep_requests=sleep_requests
            if sleep_requests is not None
            else self.sleep_requests,
            rate_limit=rate_limit if rate_limit is not None else self.rate_limit,
            concurrent_downloads=concurrent_downloads
            if concurrent_downloads is not None
            else self.concurrent_downloads,
            retries=retries if retries is not None else self.retries,
            audio_format=audio_format
            if audio_format is not None
            else self.audio_format,
            yt_dlp_bin=self.yt_dlp_bin,
            ffmpeg_bin=self.ffmpeg_bin,
            sponsorblock_categories=sponsorblock_categories
            if sponsorblock_categories is not None
            else self.sponsorblock_categories,
        )
