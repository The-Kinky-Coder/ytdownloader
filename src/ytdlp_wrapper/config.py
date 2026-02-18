"""Configuration defaults and helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    base_dir: Path = Path("/media/music")
    log_dir: Path = Path("/media/music/.logs")
    cookies_path: Path = Path("~/.config/yt-dlp/cookies.txt").expanduser()
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
        )
