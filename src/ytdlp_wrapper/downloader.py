"""Download orchestration using yt-dlp."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .config import Config
from .metadata_cache import metadata_cache_from_config
from .progress import ProgressReporter
from .utils import parse_artist_title, safe_int, sanitize


_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+\.\d+|\d+)%")
_TRACK_PREFIX_RE = re.compile(r"^(\d+)-")
_AUDIO_EXTS = {".opus", ".m4a", ".mp3", ".flac", ".ogg", ".webm", ".aac"}


@dataclass(frozen=True)
class TrackMeta:
    title: str
    artist: str
    album: str | None
    track_number: int | None
    playlist_index: int | None
    webpage_url: str


@dataclass(frozen=True)
class DownloadJob:
    key: str
    output_dir: Path
    output_stem: str
    meta: TrackMeta
    source_url: str
    m3u_path: Path | None = None

    @property
    def output_template(self) -> str:
        return str(self.output_dir / f"{self.output_stem}.%(ext)s")

    def output_filename(self, config: Config) -> str:
        return f"{self.output_stem}.{config.audio_format}"


class DownloadError(RuntimeError):
    pass


def ensure_dependencies(config: Config) -> None:
    if not shutil.which(config.yt_dlp_bin):
        raise DownloadError("yt-dlp is not installed or not on PATH.")
    if not shutil.which(config.ffmpeg_bin):
        raise DownloadError("ffmpeg is required but not on PATH.")


def ensure_log_dirs(config: Config) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)


def append_log_line(config: Config, filename: str, message: str) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = config.log_dir / filename
    timestamp = datetime.now().isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message}\n")


def copy_cookies(config: Config, cookies_path: str, logger: logging.Logger) -> Path:
    src = Path(cookies_path).expanduser()
    if not src.exists():
        raise DownloadError(f"Cookies file not found: {src}")
    config.cookies_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, config.cookies_path)
    logger.info("Copied cookies to %s", config.cookies_path)
    return config.cookies_path


def run_yt_dlp_json(
    config: Config,
    url: str,
    extra_args: Iterable[str] | None = None,
    logger: logging.Logger | None = None,
) -> dict:
    cache = metadata_cache_from_config(config)
    cached = cache.read(url, logger)
    if cached is not None:
        if not _cached_playlist_incomplete(cached, logger):
            return cached
        _log_cache_warning(logger, "Cached playlist metadata incomplete; refetching")
    args = [config.yt_dlp_bin, "-J", url]
    if extra_args:
        args.extend(extra_args)
    if config.cookies_path.exists():
        args += ["--cookies", str(config.cookies_path)]
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout
    assert process.stderr
    last_log = time.monotonic()
    stdout = ""
    stderr = ""
    while True:
        try:
            out, err = process.communicate(timeout=5)
            stdout += out or ""
            stderr += err or ""
            break
        except subprocess.TimeoutExpired:
            if logger and time.monotonic() - last_log >= 10:
                logger.info("Still fetching metadata...")
                last_log = time.monotonic()
            continue
    if process.returncode != 0 and not stdout.strip():
        raise DownloadError(
            f"yt-dlp metadata fetch failed ({process.returncode}): {stderr.strip()}"
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise DownloadError(
            f"yt-dlp metadata fetch failed ({process.returncode}): {stderr.strip()}"
        ) from exc
    if isinstance(payload, dict):
        cache.write(url, payload, logger)
    return payload


def _cached_playlist_incomplete(data: dict, logger: logging.Logger | None) -> bool:
    if not is_playlist(data):
        return False
    entries = data.get("entries") or []
    if not isinstance(entries, list):
        return False
    actual = len(entries)
    expected = (
        data.get("playlist_count")
        or data.get("entry_count")
        or data.get("entries_count")
    )
    if expected and actual < int(expected):
        _log_cache_warning(logger, "Cached playlist entries %s/%s", actual, expected)
        return True
    return False


def _log_cache_warning(
    logger: logging.Logger | None, message: str, *args: object
) -> None:
    if logger:
        logger.warning(message, *args)


def is_playlist(info: dict) -> bool:
    return info.get("_type") == "playlist" or "entries" in info


def extract_artist(info: dict) -> str | None:
    artist = info.get("artist") or info.get("uploader")
    if artist:
        return str(artist)
    artists = info.get("artists")
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict):
            return str(first.get("name") or first.get("artist"))
        return str(first)
    return None


def extract_album(info: dict) -> str | None:
    album = info.get("album")
    if album:
        return str(album)
    description = info.get("description") or ""
    if isinstance(description, str):
        match = re.search(
            r"^\s*album\s*[:\-]\s*(.+)$",
            description,
            re.IGNORECASE | re.MULTILINE,
        )
        if match:
            return match.group(1).strip()
    return None


def build_track_meta(info: dict, playlist_index: int | None) -> TrackMeta:
    title = info.get("track") or info.get("title") or "Unknown Title"
    artist = extract_artist(info)
    album = extract_album(info) or info.get("playlist")
    if not artist:
        parsed_artist, parsed_title = parse_artist_title(info.get("title") or "")
        artist = parsed_artist or "Unknown Artist"
        title = parsed_title
    return TrackMeta(
        title=str(title),
        artist=str(artist),
        album=str(album) if album else None,
        track_number=safe_int(info.get("track_number")) or None,
        playlist_index=playlist_index,
        webpage_url=str(info.get("webpage_url") or info.get("original_url") or ""),
    )


def make_output_stem(meta: TrackMeta, *, track_prefix: str | None = None) -> str:
    title = sanitize(meta.title)
    artist = sanitize(meta.artist)
    if track_prefix:
        return f"{track_prefix}-{artist}-{title}"
    return f"{artist}-{title}"


def find_existing_file(output_dir: Path, output_stem: str) -> Path | None:
    if not output_dir.exists():
        return None
    matches = list(output_dir.glob(f"{output_stem}.*"))
    return matches[0] if matches else None


def log_metadata_mismatch(
    config: Config,
    logger: logging.Logger,
    file_path: Path,
    expected: TrackMeta,
    actual: dict[str, str] | None,
) -> None:
    mismatch_log = config.log_dir / "metadata_mismatch.log"
    detail = actual or {"error": "mutagen not available"}
    line = (
        f"{file_path} | expected: artist={expected.artist} album={expected.album} "
        f"title={expected.title} track={expected.track_number} | actual: {detail}\n"
    )
    mismatch_log.parent.mkdir(parents=True, exist_ok=True)
    with mismatch_log.open("a", encoding="utf-8") as handle:
        handle.write(line)
    logger.warning("Metadata mismatch for %s", file_path)


def compare_metadata(file_path: Path, expected: TrackMeta) -> dict[str, str] | None:
    try:
        from mutagen import File  # type: ignore
    except Exception:
        return None
    audio = File(file_path)
    if not audio:
        return None
    tags = audio.tags or {}
    actual = {
        "artist": _tag_value(tags.get("artist") or tags.get("TPE1")),
        "album": _tag_value(tags.get("album") or tags.get("TALB")),
        "title": _tag_value(tags.get("title") or tags.get("TIT2")),
        "track": _tag_value(tags.get("tracknumber") or tags.get("TRCK")),
    }
    normalized = {
        "artist": expected.artist,
        "album": expected.album or "",
        "title": expected.title,
        "track": str(expected.track_number or ""),
    }
    if all(
        value in (actual.get(key) or "") for key, value in normalized.items() if value
    ):
        return None
    return actual


def _tag_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(str(item) for item in value)
    return str(value)


def build_playlist_jobs(
    config: Config, info: dict, logger: logging.Logger
) -> list[DownloadJob]:
    cache = metadata_cache_from_config(config)
    if cache.enabled:
        playlist_url = (
            info.get("webpage_url") or info.get("original_url") or info.get("url")
        )
        if playlist_url:
            cache.write(str(playlist_url), info, logger)
    entries = [entry for entry in info.get("entries") or [] if entry]
    if not entries:
        raise DownloadError("Playlist is empty or unavailable.")
    total = len(entries)
    logger.info("Preparing playlist items (%s total)...", total)
    playlist_title = sanitize(info.get("title") or "playlist")
    playlist_dir = config.base_dir / playlist_title
    playlist_m3u = playlist_dir / f"{playlist_title}.m3u"
    width = max(2, len(str(total)))
    jobs: list[DownloadJob] = []
    for index, entry in enumerate(entries, start=1):
        if index == 1 or index == total or index % 10 == 0:
            logger.info("Preparing playlist items: %s/%s", index, total)
        entry_url = entry.get("url") or entry.get("id")
        if entry_url and not str(entry_url).startswith("http"):
            entry_url = f"https://music.youtube.com/watch?v={entry_url}"
        if not entry_url:
            logger.warning("Skipping entry with no URL: %s", entry)
            continue
        meta_info: dict | None = None
        if isinstance(entry, dict):
            entry_info = dict(entry)
            if entry_url:
                entry_info["webpage_url"] = entry_url
            if info.get("title") and "playlist" not in entry_info:
                entry_info["playlist"] = info.get("title")
            if entry_info.get("title") or entry_info.get("track"):
                meta_info = entry_info
        if meta_info is None:
            try:
                meta_info = run_yt_dlp_json(
                    config,
                    str(entry_url),
                    logger=logger,
                    extra_args=["--ignore-errors"],
                )
            except DownloadError as exc:
                logger.warning("Skipping unavailable entry: %s", exc)
                append_log_line(
                    config,
                    "errors.log",
                    f"metadata failed | {entry_url} | {exc}",
                )
                continue
        if not meta_info:
            logger.warning("Skipping entry with no metadata: %s", entry_url)
            append_log_line(
                config,
                "errors.log",
                f"metadata missing | {entry_url}",
            )
            continue
        if not meta_info.get("title"):
            logger.warning("Skipping entry with missing title: %s", entry_url)
            append_log_line(
                config,
                "errors.log",
                f"metadata missing title | {entry_url}",
            )
            continue
        availability = meta_info.get("availability")
        if availability and str(availability).lower() != "public":
            logger.warning(
                "Skipping unavailable entry: %s (%s)", entry_url, availability
            )
            append_log_line(
                config,
                "errors.log",
                f"metadata unavailable | {entry_url} | {availability}",
            )
            continue
        if config.sleep_requests > 0:
            time.sleep(config.sleep_requests)
        playlist_index = safe_int(entry.get("playlist_index"), default=index)
        meta = build_track_meta(meta_info, playlist_index)
        if meta.title.lower() in {"index", "videoplayback"}:
            logger.warning("Skipping entry with invalid title: %s", entry_url)
            append_log_line(
                config,
                "errors.log",
                f"metadata invalid title | {entry_url} | {meta.title}",
            )
            continue
        source_url = (
            meta_info.get("webpage_url") or meta_info.get("original_url") or entry_url
        )
        video_id = meta_info.get("id")
        if video_id:
            source_url = f"https://music.youtube.com/watch?v={video_id}"
        track_number = meta.track_number or playlist_index or index
        prefix = str(track_number).zfill(width)
        stem = make_output_stem(meta, track_prefix=prefix)
        jobs.append(
            DownloadJob(
                key=f"{prefix}-{sanitize(meta.title)}",
                output_dir=playlist_dir,
                output_stem=stem,
                meta=meta,
                source_url=str(source_url),
                m3u_path=playlist_m3u,
            )
        )
    return jobs


def build_single_job(config: Config, info: dict) -> DownloadJob:
    meta = build_track_meta(info, playlist_index=None)
    artist_dir = sanitize(meta.artist)
    album_dir = sanitize(meta.album or "Unknown Album")
    output_dir = config.base_dir / artist_dir / album_dir
    stem = make_output_stem(meta)
    source_url = str(info.get("webpage_url") or info.get("original_url") or "")
    if not source_url:
        source_url = ""
    return DownloadJob(
        key=sanitize(meta.title),
        output_dir=output_dir,
        output_stem=stem,
        meta=meta,
        source_url=source_url,
    )


def yt_dlp_args(config: Config, job: DownloadJob) -> list[str]:
    args = [
        config.yt_dlp_bin,
        "--newline",
        "--continue",
        "--no-overwrites",
        "--extract-audio",
        "--audio-format",
        config.audio_format,
        "--embed-metadata",
        "--embed-thumbnail",
        "--add-metadata",
        "-f",
        "bestaudio",
        "--download-archive",
        str(config.download_archive),
        "-o",
        job.output_template,
    ]
    if config.rate_limit:
        args += ["--rate-limit", config.rate_limit]
    args += ["--sleep-interval", str(config.sleep_interval)]
    args += ["--max-sleep-interval", str(config.max_sleep_interval)]
    args += ["--retries", str(config.retries)]
    if config.cookies_path.exists():
        args += ["--cookies", str(config.cookies_path)]
    return args


def download_job(
    config: Config,
    job: DownloadJob,
    logger: logging.Logger,
    progress: ProgressReporter,
) -> None:
    source_url = job.source_url or job.meta.webpage_url
    job.output_dir.mkdir(parents=True, exist_ok=True)
    existing = find_existing_file(job.output_dir, job.output_stem)
    if existing:
        actual = compare_metadata(existing, job.meta)
        if actual is not None:
            log_metadata_mismatch(config, logger, existing, job.meta, actual)
        logger.info("Skipping: %s (already exists)", existing.name)
        append_log_line(
            config,
            "skipped.log",
            f"{job.output_stem} | {existing} | {source_url}",
        )
        progress.advance_overall()
        return

    args = yt_dlp_args(config, job)
    args.append(source_url)
    progress.add_task(job.key, job.output_stem, total=100)

    for attempt in range(1, config.retries + 1):
        logger.info(
            "Downloading %s (attempt %s/%s)", job.output_stem, attempt, config.retries
        )
        if attempt > 1:
            append_log_line(
                config,
                "retries.log",
                f"{job.output_stem} | attempt {attempt}/{config.retries} | {source_url}",
            )
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout
        last_lines: deque[str] = deque(maxlen=20)
        for line in process.stdout:
            last_lines.append(line.rstrip())
            match = _PROGRESS_RE.search(line)
            if match:
                progress.update(job.key, completed=float(match.group(1)))
        returncode = process.wait()
        if returncode == 0:
            logger.info("Completed: %s", job.output_stem)
            append_log_line(
                config,
                "success.log",
                f"{job.output_stem} | {job.output_dir} | {source_url}",
            )
            progress.complete(job.key)
            return
        logger.error("Download failed for %s: exit %s", job.output_stem, returncode)
        if last_lines:
            append_log_line(
                config,
                "errors.log",
                f"{job.output_stem} | last output: {' | '.join(last_lines)}",
            )
        append_log_line(
            config,
            "errors.log",
            f"{job.output_stem} | exit {returncode} | {source_url}",
        )
    append_log_line(
        config,
        "errors.log",
        f"{job.output_stem} | failed after {config.retries} retries | {source_url}",
    )
    raise DownloadError(f"Failed to download {job.output_stem} after retries.")


def download_url(config: Config, url: str, logger: logging.Logger) -> None:
    logger.info("Fetching metadata from YouTube Music...")
    info = run_yt_dlp_json(
        config,
        url,
        logger=logger,
        extra_args=["--ignore-errors", "--flat-playlist"],
    )
    if is_playlist(info):
        jobs = build_playlist_jobs(config, info, logger)
    else:
        jobs = [build_single_job(config, info)]

    logger.info("Starting downloads: %s item(s)", len(jobs))
    download_error: BaseException | None = None
    with ProgressReporter(total=len(jobs), logger=logger) as progress:
        with ThreadPoolExecutor(max_workers=config.concurrent_downloads) as executor:
            futures = [
                executor.submit(download_job, config, job, logger, progress)
                for job in jobs
            ]
            for future in as_completed(futures):
                exception = future.exception()
                if exception is not None and download_error is None:
                    logger.error("Download error: %s", exception)
                    append_log_line(
                        config,
                        "errors.log",
                        f"{exception}",
                    )
                    download_error = exception
    if jobs and jobs[0].m3u_path:
        write_playlist_m3u(config, jobs, logger)
    if download_error is not None:
        raise download_error


def write_playlist_m3u(
    config: Config, jobs: list[DownloadJob], logger: logging.Logger
) -> None:
    if not jobs:
        return
    m3u_path = jobs[0].m3u_path
    if not m3u_path:
        return
    lines: list[str] = ["#EXTM3U"]
    missing = 0
    for job in jobs:
        file_path = job.output_dir / job.output_filename(config)
        if not file_path.exists():
            missing += 1
            continue
        extinf = f"#EXTINF:-1,{job.meta.artist} - {job.meta.title}"
        try:
            relative = file_path.relative_to(config.base_dir)
            entry = str(relative)
        except ValueError:
            entry = str(file_path)
        lines.append(extinf)
        lines.append(entry)
    m3u_path.parent.mkdir(parents=True, exist_ok=True)
    m3u_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if missing:
        logger.warning("Playlist M3U missing %s files", missing)


def rewrite_m3u_from_dir(
    playlist_dir: Path, config: Config, logger: logging.Logger
) -> None:
    if not playlist_dir.exists() or not playlist_dir.is_dir():
        raise DownloadError(f"Playlist directory not found: {playlist_dir}")
    playlist_name = playlist_dir.name
    m3u_path = playlist_dir / f"{playlist_name}.m3u"
    files = [
        path
        for path in playlist_dir.iterdir()
        if path.is_file() and path.suffix.lower() in _AUDIO_EXTS
    ]
    files.sort(key=_track_sort_key)
    lines: list[str] = ["#EXTM3U"]
    for path in files:
        artist, title = _extract_tags(path)
        extinf = f"#EXTINF:-1,{artist} - {title}"
        try:
            relative = path.relative_to(config.base_dir)
            entry = str(relative)
        except ValueError:
            entry = str(path)
        lines.append(extinf)
        lines.append(entry)
    m3u_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Rewrote playlist M3U: %s", m3u_path)


def rewrite_all_m3u(config: Config, logger: logging.Logger) -> None:
    if not config.base_dir.exists():
        raise DownloadError(f"Base directory not found: {config.base_dir}")
    folders = [
        path
        for path in config.base_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    if not folders:
        logger.info("No playlist folders found under %s", config.base_dir)
        return
    for folder in folders:
        rewrite_m3u_from_dir(folder, config, logger)


def _track_sort_key(path: Path) -> tuple[int, str]:
    match = _TRACK_PREFIX_RE.match(path.name)
    if match:
        return int(match.group(1)), path.name
    return 0, path.name


def _extract_tags(path: Path) -> tuple[str, str]:
    try:
        from mutagen import File  # type: ignore
    except Exception:
        return _fallback_artist_title(path)
    audio = File(path)
    if not audio:
        return _fallback_artist_title(path)
    tags = audio.tags or {}
    artist = _tag_value(tags.get("artist") or tags.get("TPE1"))
    title = _tag_value(tags.get("title") or tags.get("TIT2"))
    if artist and title:
        return artist, title
    return _fallback_artist_title(path)


def _fallback_artist_title(path: Path) -> tuple[str, str]:
    name = path.stem
    name = _TRACK_PREFIX_RE.sub("", name)
    artist, title = parse_artist_title(name)
    return artist or "Unknown Artist", title
