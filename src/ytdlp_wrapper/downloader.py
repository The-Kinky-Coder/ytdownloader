"""Download orchestration using yt-dlp."""

from __future__ import annotations

import glob
import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from collections import deque
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .config import Config
from .metadata_cache import metadata_cache_from_config
from .pending import (
    PENDING_TASK_SPONSORBLOCK,
    PendingFile,
    find_pending_sidecars,
    write_pending,
)
from .progress import ProgressReporter
from .sponsorblock_local import extract_video_id, fetch_segments, remove_segments_ffmpeg
from .utils import parse_artist_title, safe_int, sanitize


_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+\.\d+|\d+)%")
_TRACK_PREFIX_RE = re.compile(r"^(\d+)-")
_AUDIO_EXTS = {".opus", ".m4a", ".mp3", ".flac", ".ogg", ".webm", ".aac"}
_M3U_PLAYLIST_URL_PREFIX = "#PLAYLIST-URL:"

# Phrase emitted by yt-dlp when the SponsorBlock API is unreachable or returns
# a server error.  We match on this substring to distinguish a SponsorBlock-
# only failure (file downloaded fine) from a genuine download failure.
_SPONSORBLOCK_API_ERROR_PHRASE = "Unable to communicate with SponsorBlock API"

# How many times to retry SponsorBlock post-processing after the main
# playlist download finishes, before giving up and keeping the un-trimmed file.
_SPONSORBLOCK_RETRY_ATTEMPTS = 3

# Query parameters that carry functional meaning and should be kept.
# Everything else (si, feature, pp, utm_*, etc.) is tracking noise.
_KEEP_QUERY_PARAMS = {"list", "v"}


def clean_playlist_url(url: str) -> str:
    """Strip tracking/session query params from a YouTube Music URL.

    Keeps only the params in _KEEP_QUERY_PARAMS (i.e. ``list`` and ``v``).
    Everything else — ``si``, ``feature``, ``pp``, ``utm_*``, etc. — is dropped.

    >>> clean_playlist_url(
    ...     "https://music.youtube.com/playlist?list=PLxxx&si=jYFdmA5CdprIdmsH"
    ... )
    'https://music.youtube.com/playlist?list=PLxxx'
    """
    parsed = urlparse(url)
    kept = [(k, v) for k, v in parse_qsl(parsed.query) if k in _KEEP_QUERY_PARAMS]
    clean = parsed._replace(query=urlencode(kept))
    return urlunparse(clean)


_DEFAULT_SPONSORBLOCK_CONTENT = """\
# SponsorBlock categories to remove during download.
# One category per line. Lines starting with # are ignored.
#
# Available categories:
#   sponsor        - Paid promotion / advertisements
#   selfpromo      - Unpaid self-promotion (merch, Patreon, etc.)
#   interaction    - Requests to like, subscribe, follow, etc.
#   intro          - Intro animation / title card
#   outro          - Outro / end-cards
#   preview        - Preview of content in the video
#   music_offtopic - Non-music section in a music video (e.g. speech)
#   poi_highlight  - Highlight point of the video (kept by default, not removed)
#   filler         - Filler content not relevant to the main topic
#
# Leave this file empty (or remove all non-comment lines) to disable SponsorBlock.
#
# Reference: https://wiki.sponsor.ajay.app/w/Segment_Categories
music_offtopic
sponsor
selfpromo
intro
outro
"""

_DEFAULT_SPONSORBLOCK_CATEGORIES = (
    "music_offtopic",
    "sponsor",
    "selfpromo",
    "intro",
    "outro",
)


def load_sponsorblock_categories(
    config_path: Path, logger: logging.Logger | None = None
) -> tuple[str, ...]:
    """Read the SponsorBlock config file and return a tuple of category strings.

    Lines starting with '#' and blank lines are ignored.
    If the file does not exist it is created with the default categories so the
    user has a ready-to-edit file and the warning never appears again.
    Returns an empty tuple only if the file exists but contains no active lines,
    which disables SponsorBlock entirely (no --sponsorblock-remove flag is added).
    """
    resolved = (
        config_path.expanduser() if not config_path.is_absolute() else config_path
    )
    if not resolved.exists():
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(_DEFAULT_SPONSORBLOCK_CONTENT, encoding="utf-8")
        if logger:
            logger.info(
                "Created default SponsorBlock config at %s — using defaults: %s",
                resolved,
                ", ".join(_DEFAULT_SPONSORBLOCK_CATEGORIES),
            )
        return _DEFAULT_SPONSORBLOCK_CATEGORIES
    categories: list[str] = []
    for raw_line in resolved.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            categories.append(line)
    if logger:
        if categories:
            logger.info(
                "SponsorBlock enabled — removing categories: %s",
                ", ".join(categories),
            )
        else:
            logger.info(
                "SponsorBlock config %s has no active categories — SponsorBlock disabled.",
                resolved,
            )
    return tuple(categories)


@dataclass(frozen=True)
class TrackMeta:
    title: str
    artist: str
    album: str | None
    album_artist: str | None
    compilation: bool
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


def build_track_meta(
    info: dict,
    playlist_index: int | None,
    *,
    playlist_title: str | None = None,
    is_compilation: bool = False,
) -> TrackMeta:
    """Build a :class:`TrackMeta` from a yt-dlp info dict.

    For playlist downloads pass *playlist_title* (the sanitised folder name) so
    every track shares the same ``album`` value, and set *is_compilation=True*
    so that ``ALBUMARTIST`` and ``COMPILATION`` tags are applied via mutagen
    after the download.  Navidrome requires both of those tags to group tracks
    from a various-artists playlist into a single album entry.
    """
    title = info.get("track") or info.get("title") or "Unknown Title"
    artist = extract_artist(info)
    # For playlists the folder/playlist name is always used as the album so
    # that every track ends up under the same album in Navidrome.
    album = playlist_title or extract_album(info) or info.get("playlist")
    if not artist:
        parsed_artist, parsed_title = parse_artist_title(info.get("title") or "")
        artist = parsed_artist or "Unknown Artist"
        title = parsed_title
    album_artist = "Various Artists" if is_compilation else str(artist)
    return TrackMeta(
        title=str(title),
        artist=str(artist),
        album=str(album) if album else None,
        album_artist=album_artist,
        compilation=is_compilation,
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
    escaped = glob.escape(output_stem)
    matches = [
        p
        for p in output_dir.glob(f"{escaped}.*")
        if ".temp." not in p.name and not p.name.endswith(".pending.json")
    ]
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
        f"{file_path} | expected: album={expected.album} albumartist={expected.album_artist} "
        f"title={expected.title} compilation={int(expected.compilation)} | actual: {detail}\n"
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
        "album": _tag_value(tags.get("album") or tags.get("TALB")),
        "title": _tag_value(tags.get("title") or tags.get("TIT2")),
        "albumartist": _tag_value(
            tags.get("albumartist") or tags.get("album_artist") or tags.get("TPE2")
        ),
        "compilation": _tag_value(tags.get("compilation")),
    }
    # Only check tags that are critical for Navidrome grouping.
    # artist is intentionally excluded — flat playlist entries use channel names
    # (e.g. "ReelBigFishVIDEOS", "Catch 22 - Topic") which differ from the
    # actual artist tag yt-dlp embeds, causing unavoidable false positives.
    normalized = {
        "album": expected.album or "",
        "title": expected.title,
        "albumartist": expected.album_artist or "",
        "compilation": "1" if expected.compilation else "",
    }

    def _loose_match(expected_val: str, actual_val: str) -> bool:
        """Case-insensitive, whitespace-collapsed substring match."""

        def _norm(s: str) -> str:
            return "".join(s.lower().split())

        return _norm(expected_val) in _norm(actual_val)

    if all(
        _loose_match(value, actual.get(key) or "")
        for key, value in normalized.items()
        if value
    ):
        return None
    return actual


def _tag_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(str(item) for item in value)
    return str(value)


# ---------------------------------------------------------------------------
# Mutagen-based tag writing for Navidrome compilation support
# ---------------------------------------------------------------------------


def _write_compilation_tags_mutagen(
    file_path: Path,
    album: str,
    album_artist: str,
    compilation: bool,
    track_number: int | None,
    logger: logging.Logger,
) -> bool:
    """Write ALBUMARTIST, ALBUM, COMPILATION (and optionally TRACKNUMBER) tags.

    Returns True on success.  Returns False if mutagen is unavailable or the
    write fails (e.g. unsupported/corrupt file).

    Tag format mapping
    ------------------
    - Ogg Opus / Ogg Vorbis / FLAC  → Vorbis comments
      ALBUMARTIST, ALBUM, COMPILATION=1, TRACKNUMBER
    - MP3 → ID3v2
      TPE2 (Album Artist), TALB (Album), TCMP=1, TRCK
    - M4A/MP4 → MP4 atoms
      aART (Album Artist), ©alb (Album), cpil=True, trkn
    """
    try:
        from mutagen import File as MutagenFile  # type: ignore
    except ImportError:
        logger.warning(
            "mutagen not installed; skipping tag write for %s", file_path.name
        )
        return False

    try:
        audio = MutagenFile(file_path, easy=False)
        if audio is None:
            logger.warning("mutagen could not open %s", file_path.name)
            return False

        ext = file_path.suffix.lower()

        # ── Vorbis comments (Ogg Opus, Ogg Vorbis, FLAC) ───────────────────
        if ext in (".opus", ".ogg", ".flac"):
            tags = audio.tags
            if tags is None:
                audio.add_tags()
                tags = audio.tags
            tags["ALBUMARTIST"] = [album_artist]
            tags["ALBUM"] = [album]
            tags["COMPILATION"] = ["1" if compilation else "0"]
            if track_number is not None:
                tags["TRACKNUMBER"] = [str(track_number)]
            audio.save()
            return True

        # ── ID3 (MP3) ────────────────────────────────────────────────────────
        if ext == ".mp3":
            from mutagen.id3 import ID3, TALB, TCMP, TPE2, TRCK  # type: ignore

            try:
                id3 = ID3(file_path)
            except Exception:
                id3 = ID3()
            id3["TPE2"] = TPE2(encoding=3, text=[album_artist])
            id3["TALB"] = TALB(encoding=3, text=[album])
            id3["TCMP"] = TCMP(encoding=3, text=["1" if compilation else "0"])
            if track_number is not None:
                id3["TRCK"] = TRCK(encoding=3, text=[str(track_number)])
            id3.save(file_path)
            return True

        # ── MP4 / M4A / AAC ─────────────────────────────────────────────────
        if ext in (".m4a", ".mp4", ".aac"):
            from mutagen.mp4 import MP4  # type: ignore

            mp4 = MP4(file_path)
            if mp4.tags is None:
                mp4.add_tags()
            mp4.tags["aART"] = [album_artist]
            mp4.tags["\xa9alb"] = [album]
            mp4.tags["cpil"] = compilation
            if track_number is not None:
                mp4.tags["trkn"] = [(track_number, 0)]
            mp4.save()
            return True

        # ── Generic fallback via easy tags ───────────────────────────────────
        audio_easy = MutagenFile(file_path, easy=True)
        if audio_easy is not None:
            if audio_easy.tags is None:
                audio_easy.add_tags()
            audio_easy.tags["albumartist"] = [album_artist]
            audio_easy.tags["album"] = [album]
            audio_easy.save()
            return True

        logger.warning(
            "Unsupported tag format for %s; skipping tag write", file_path.name
        )
        return False

    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write tags for %s: %s", file_path.name, exc)
        return False


def apply_compilation_tags(
    file_path: Path,
    meta: TrackMeta,
    logger: logging.Logger,
) -> bool:
    """Write ALBUMARTIST / ALBUM / COMPILATION tags to *file_path* using mutagen.

    Called after a successful download to ensure Navidrome groups the track
    correctly as part of its playlist-album rather than creating one "album"
    per individual track artist.
    """
    if not meta.album or not meta.album_artist:
        return False
    return _write_compilation_tags_mutagen(
        file_path=file_path,
        album=meta.album,
        album_artist=meta.album_artist,
        compilation=meta.compilation,
        track_number=meta.track_number or meta.playlist_index,
        logger=logger,
    )


def retag_playlist_dir(
    playlist_dir: Path,
    config: Config,
    logger: logging.Logger,
    album_artist: str = "Various Artists",
    compilation: bool = True,
) -> int:
    """Retroactively fix Navidrome compilation tags on an existing playlist folder.

    Sets ALBUM to the folder name, ALBUMARTIST to *album_artist* (default
    ``"Various Artists"``), and COMPILATION=1 on every audio file in
    *playlist_dir*.  Returns the number of files successfully retagged.

    This is used to fix libraries downloaded before compilation tagging was
    implemented.  Run with ``--retag <directory>`` or ``--retag-all``.
    """
    if not playlist_dir.exists() or not playlist_dir.is_dir():
        raise DownloadError(f"Playlist directory not found: {playlist_dir}")

    album_name = playlist_dir.name
    files = [
        p
        for p in playlist_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTS
    ]
    files.sort(key=_track_sort_key)

    updated = 0
    for i, file_path in enumerate(files, start=1):
        track_match = _TRACK_PREFIX_RE.match(file_path.name)
        track_number = int(track_match.group(1)) if track_match else i
        ok = _write_compilation_tags_mutagen(
            file_path=file_path,
            album=album_name,
            album_artist=album_artist,
            compilation=compilation,
            track_number=track_number,
            logger=logger,
        )
        if ok:
            logger.info("Retagged: %s", file_path.name)
            updated += 1
        else:
            logger.warning("Could not retag: %s", file_path.name)

    logger.info("Retagged %s/%s files in '%s'", updated, len(files), playlist_dir.name)
    return updated


def retag_all_playlist_dirs(config: Config, logger: logging.Logger) -> None:
    """Run :func:`retag_playlist_dir` on every subdirectory of *config.base_dir*."""
    if not config.base_dir.exists():
        raise DownloadError(f"Base directory not found: {config.base_dir}")
    folders = [
        p
        for p in config.base_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    ]
    if not folders:
        logger.info("No playlist folders found under %s", config.base_dir)
        return
    total_updated = 0
    for folder in folders:
        total_updated += retag_playlist_dir(folder, config, logger)
    logger.info("Total files retagged: %s", total_updated)


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
            meta_info = cache.read(str(entry_url), logger)
            if meta_info is None:
                if config.sleep_requests > 0:
                    time.sleep(config.sleep_requests)
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
                if meta_info:
                    cache.write(str(entry_url), meta_info, logger)
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
        playlist_index = safe_int(entry.get("playlist_index"), default=index)
        meta = build_track_meta(
            meta_info,
            playlist_index,
            playlist_title=playlist_title,
            is_compilation=True,
        )
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


def scrub_archive(
    config: Config, jobs: list[DownloadJob], logger: logging.Logger
) -> int:
    """Remove download archive entries whose output files are missing from disk.

    When a file is deleted from disk but its video ID remains in the archive,
    yt-dlp silently skips it on the next run. This function detects that case
    and removes those stale entries so yt-dlp will re-download them.

    Returns the number of entries removed.
    """
    archive_path = config.download_archive
    if not archive_path.exists():
        return 0

    # Build a map of video_id -> job for quick lookup
    id_to_job: dict[str, DownloadJob] = {}
    for job in jobs:
        parsed = urlparse(job.source_url)
        params = dict(parse_qsl(parsed.query))
        video_id = params.get("v") or parsed.path.lstrip("/")
        if video_id:
            id_to_job[video_id] = job

    lines = archive_path.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    removed = 0
    for line in lines:
        parts = line.strip().split()
        if len(parts) >= 2:
            video_id = parts[1]
            job = id_to_job.get(video_id)
            if job is not None:
                existing = find_existing_file(job.output_dir, job.output_stem)
                if existing is None:
                    logger.warning(
                        "Archive scrub: removing stale entry %s (%s) — file missing from disk, will re-download",
                        video_id,
                        job.output_stem,
                    )
                    removed += 1
                    continue
        kept.append(line)

    if removed:
        archive_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        logger.info("Archive scrub: removed %d stale entries", removed)
    return removed


def build_single_job(config: Config, info: dict) -> DownloadJob:
    meta = build_track_meta(info, playlist_index=None, is_compilation=False)
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
    if config.sponsorblock_categories:
        args += ["--sponsorblock-remove", ",".join(config.sponsorblock_categories)]
    return args


def download_job(
    config: Config,
    job: DownloadJob,
    logger: logging.Logger,
    progress: ProgressReporter,
    sponsorblock_retry_queue: "list[DownloadJob] | None" = None,
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
        # Still apply compilation tags on skipped files so that files downloaded
        # before compilation tagging was introduced are silently self-healed on
        # the next normal download run, without requiring --retag-all.
        if job.meta.compilation or job.meta.album_artist:
            apply_compilation_tags(existing, job.meta, logger)
        progress.advance_overall()
        return

    args = yt_dlp_args(config, job)
    args.append(source_url)
    progress.add_task(job.key, job.output_stem, total=100)

    for attempt in range(1, config.retries + 1):
        logger.debug(
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
            # Write ALBUMARTIST / ALBUM / COMPILATION tags so Navidrome groups
            # all tracks in a playlist into one album rather than splitting them
            # per individual track artist.
            downloaded_file = find_existing_file(job.output_dir, job.output_stem)
            if downloaded_file and (job.meta.compilation or job.meta.album_artist):
                apply_compilation_tags(downloaded_file, job.meta, logger)
            progress.complete(job.key)
            return
        # If the only failure was the SponsorBlock API being unreachable, the
        # audio file was still downloaded correctly — yt-dlp just couldn't trim
        # sponsor segments.  Treat this as a soft failure: mark the track as
        # completed and queue it for a SponsorBlock-only retry pass after the
        # rest of the playlist finishes.
        if _is_sponsorblock_api_error(last_lines):
            logger.warning(
                "SponsorBlock API error for %s — file downloaded, will retry "
                "SponsorBlock post-processing after playlist completes.",
                job.output_stem,
            )
            downloaded_file = find_existing_file(job.output_dir, job.output_stem)
            if downloaded_file and (job.meta.compilation or job.meta.album_artist):
                apply_compilation_tags(downloaded_file, job.meta, logger)
            # Write a sidecar so the user can re-apply SponsorBlock later via
            # --retry-sponsorblock even if the in-session retry also fails.
            if downloaded_file and config.sponsorblock_categories:
                write_pending(
                    downloaded_file,
                    source_url,
                    job.output_stem,
                    [PENDING_TASK_SPONSORBLOCK],
                    logger=logger,
                )
            progress.complete(job.key)
            if sponsorblock_retry_queue is not None and config.sponsorblock_categories:
                sponsorblock_retry_queue.append(job)
            return
        reason = _extract_failure_reason(last_lines, returncode)
        logger.error("Download failed for %s: %s", job.output_stem, reason)
        append_log_line(
            config,
            "errors.log",
            f"{job.output_stem} | exit {returncode} | {reason} | {source_url}",
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
    playlist_url: str | None = None
    if is_playlist(info):
        raw_url = info.get("webpage_url") or info.get("original_url") or url or None
        playlist_url = clean_playlist_url(raw_url) if raw_url else None
        jobs = build_playlist_jobs(config, info, logger)
    else:
        jobs = [build_single_job(config, info)]

    logger.info("Starting downloads: %s item(s)", len(jobs))
    scrub_archive(config, jobs, logger)
    sponsorblock_retry_queue: list[DownloadJob] = []
    download_error: BaseException | None = None
    with ProgressReporter(total=len(jobs), logger=logger) as progress:
        with ThreadPoolExecutor(max_workers=config.concurrent_downloads) as executor:
            futures = [
                executor.submit(
                    download_job,
                    config,
                    job,
                    logger,
                    progress,
                    sponsorblock_retry_queue,
                )
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
        write_playlist_m3u(config, jobs, logger, playlist_url=playlist_url)

    # --- SponsorBlock retry pass ---
    # Tracks whose audio downloaded fine but whose SponsorBlock post-processing
    # failed due to a transient API error are queued here.  We retry them now
    # that the main playlist download is complete, giving the API time to recover.
    if sponsorblock_retry_queue:
        logger.info(
            "Retrying SponsorBlock post-processing for %d track(s)…",
            len(sponsorblock_retry_queue),
        )
        sb_failed: list[str] = []
        for job in sponsorblock_retry_queue:
            success = _retry_sponsorblock_for_job(config, job, logger)
            if not success:
                sb_failed.append(job.output_stem)
        if sb_failed:
            logger.warning(
                "SponsorBlock post-processing failed for %d track(s) after retries. "
                "Files are kept but sponsor segments were NOT removed: %s",
                len(sb_failed),
                ", ".join(sb_failed),
            )
            for stem in sb_failed:
                append_log_line(
                    config,
                    "errors.log",
                    f"{stem} | SponsorBlock API unreachable after retries — segments not removed",
                )
        else:
            logger.info(
                "SponsorBlock post-processing retry succeeded for all %d track(s).",
                len(sponsorblock_retry_queue),
            )

    if download_error is not None:
        raise download_error


def write_playlist_m3u(
    config: Config,
    jobs: list[DownloadJob],
    logger: logging.Logger,
    playlist_url: str | None = None,
) -> None:
    if not jobs:
        return
    m3u_path = jobs[0].m3u_path
    if not m3u_path:
        return
    lines: list[str] = ["#EXTM3U"]
    if playlist_url:
        lines.append(f"{_M3U_PLAYLIST_URL_PREFIX}{playlist_url}")
    missing_stems: list[str] = []
    for job in jobs:
        file_path = job.output_dir / job.output_filename(config)
        if not file_path.exists():
            # Fall back to glob search in case the file exists under a different extension
            file_path = find_existing_file(job.output_dir, job.output_stem) or file_path
        if not file_path.exists():
            missing_stems.append(job.output_stem)
            continue
        extinf = f"#EXTINF:-1,{job.meta.artist} - {job.meta.title}"
        try:
            relative = file_path.relative_to(config.base_dir)
            entry = relative.as_posix()
        except ValueError:
            entry = file_path.as_posix()
        lines.append(extinf)
        lines.append(entry)
    m3u_path.parent.mkdir(parents=True, exist_ok=True)
    m3u_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if missing_stems:
        logger.warning("Playlist M3U missing %d files:", len(missing_stems))
        for stem in missing_stems:
            logger.warning("  missing: %s", stem)


def _cleanup_temp_sidecars(base_dir: Path, logger: logging.Logger) -> None:
    """Delete yt-dlp temporary sidecar artifacts (e.g. foo.temp.pending.json).

    These are left behind when a reprocess run is interrupted or when yt-dlp
    writes a temp file alongside the audio during download and fails to clean
    up.  They are safe to delete unconditionally.
    """
    from .pending import _SIDECAR_SUFFIX

    removed = 0
    for path in base_dir.rglob(f"*.temp{_SIDECAR_SUFFIX}"):
        try:
            path.unlink()
            logger.debug("Removed temp sidecar artifact: %s", path.name)
            removed += 1
        except OSError as exc:
            logger.warning("Could not remove temp sidecar %s: %s", path, exc)
    if removed:
        logger.info("Removed %d temporary sidecar artifact(s).", removed)


def _bootstrap_pending_from_logs(
    config: Config,
    logger: logging.Logger,
) -> int:
    """One-time migration: scan errors.log for historic SponsorBlock failures.

    Parses errors.log for lines containing the SponsorBlock API error string,
    extracts the source URL (last pipe-delimited field), supplements missing
    directory info from success.log, then writes sidecars for any audio files
    found on disk that don't already have one.

    Returns the number of sidecars created.
    """
    errors_log = config.log_dir / "errors.log"
    success_log = config.log_dir / "success.log"
    if not errors_log.exists():
        return 0

    # Markers that indicate a SponsorBlock failure — stems matching these lines
    # are candidates for sidecar creation.
    _SB_FAILURE_MARKERS = (
        "Unable to communicate with SponsorBlock API",
        "SponsorBlock API unreachable after retries",  # written by our own retry path
    )
    # Marker written when SponsorBlock confirmed no segments exist for the track.
    # Stems with this marker are permanently resolved — never create a sidecar.
    _SB_RESOLVED_MARKER = "SponsorBlock resolved — no segments in database"

    # Collect stems that have a persistent SponsorBlock failure recorded.
    # errors.log format:
    #   TIMESTAMP stem | exit 1 | ERROR: Preprocessing: Unable to communicate with SponsorBlock API... | https://...
    # The URL is the last |-delimited field; it may also be absent in older log lines.
    stem_to_url: dict[str, str] = {}
    resolved_stems: set[str] = set()
    for line in errors_log.read_text(encoding="utf-8").splitlines():
        rest = line.split(" ", 1)[-1]
        parts = [p.strip() for p in rest.split(" | ")]
        if not parts:
            continue
        stem = parts[0]
        if not stem:
            continue
        if _SB_RESOLVED_MARKER in line:
            # This stem was confirmed clean — exclude it permanently.
            resolved_stems.add(stem)
            continue
        if not any(m in line for m in _SB_FAILURE_MARKERS):
            continue
        # The URL is the last field when present (and starts with "http").
        url = ""
        if len(parts) >= 2 and parts[-1].startswith("http"):
            url = parts[-1]
        stem_to_url[stem] = url

    # Remove any stems that were later confirmed as having no segments.
    if resolved_stems:
        stem_to_url = {s: u for s, u in stem_to_url.items() if s not in resolved_stems}

    if not stem_to_url:
        return 0

    # Supplement missing URLs from success.log when available.
    # success.log format: "TIMESTAMP stem | /abs/output/dir | https://..."
    stem_to_dir: dict[str, Path] = {}
    if success_log.exists():
        for line in success_log.read_text(encoding="utf-8").splitlines():
            rest = line.split(" ", 1)[-1]
            parts = [p.strip() for p in rest.split(" | ")]
            if len(parts) >= 3:
                s = parts[0]
                if s in stem_to_url:
                    if not stem_to_url[s]:
                        stem_to_url[s] = parts[2]
                    stem_to_dir[s] = Path(parts[1])

    created = 0
    for stem, source_url in stem_to_url.items():
        output_dir = stem_to_dir.get(stem)
        if output_dir is None:
            # Fall back to a full recursive search under base_dir.
            audio_file = find_existing_file(Path(config.base_dir), stem)
            if audio_file is None:
                logger.debug(
                    "Bootstrap: audio file for %s not found under %s — skipping.",
                    stem,
                    config.base_dir,
                )
                continue
        else:
            audio_file = find_existing_file(output_dir, stem)
            if audio_file is None:
                logger.debug(
                    "Bootstrap: audio file for %s not found in %s — skipping.",
                    stem,
                    output_dir,
                )
                continue
        from .pending import audio_file_to_sidecar

        if audio_file_to_sidecar(audio_file).exists():
            logger.debug("Bootstrap: sidecar already exists for %s — skipping.", stem)
            continue
        write_pending(
            audio_file, source_url, stem, [PENDING_TASK_SPONSORBLOCK], logger=logger
        )
        logger.info("Bootstrap: created sidecar for %s", stem)
        created += 1

    return created


def process_pending_sponsorblock(config: Config, logger: logging.Logger) -> None:
    """Retry SponsorBlock post-processing for all files with a pending sidecar.

    This is the entry point for ``--retry-sponsorblock``.  It:

    1. Runs a one-time log bootstrap to create sidecars for any historic
       errors recorded before the sidecar system was introduced.
    2. Scans ``base_dir`` recursively for ``*.pending.json`` files that
       contain the ``sponsorblock`` task.
    3. For each candidate, verifies the audio file is on disk and a source
       URL is available, then re-runs yt-dlp with ``--sponsorblock-remove``.
    4. On success, removes the ``sponsorblock`` task from the sidecar (and
       deletes the sidecar if it becomes empty).
    5. On persistent failure, logs a warning and leaves the sidecar in place
       for the next attempt.
    """
    if not config.sponsorblock_categories:
        logger.warning(
            "--retry-sponsorblock: SponsorBlock is disabled (no categories configured). "
            "Set sponsorblock_categories in config.ini first."
        )
        return

    # Clean up any yt-dlp temporary sidecar artifacts left behind by failed
    # reprocess runs (e.g. foo.temp.pending.json).
    _cleanup_temp_sidecars(config.base_dir, logger)

    # Bootstrap sidecars from historic log entries so that files that failed
    # before the sidecar system was added are also picked up.
    bootstrapped = _bootstrap_pending_from_logs(config, logger)
    if bootstrapped:
        logger.info(
            "Bootstrapped %d sidecar(s) from historic errors.log entries.", bootstrapped
        )

    pending = find_pending_sidecars(
        config.base_dir, task=PENDING_TASK_SPONSORBLOCK, logger=logger
    )

    if not pending:
        logger.info("No pending SponsorBlock tasks found under %s.", config.base_dir)
        return

    logger.info(
        "Found %d file(s) with pending SponsorBlock post-processing.", len(pending)
    )

    succeeded: list[str] = []
    failed: list[str] = []

    for pf in pending:
        if not pf.source_url:
            logger.warning("Skipping %s — sidecar has no source_url.", pf.output_stem)
            failed.append(pf.output_stem)
            continue
        if not pf.audio_file.exists():
            logger.warning(
                "Skipping %s — audio file no longer exists at %s.",
                pf.output_stem,
                pf.audio_file,
            )
            failed.append(pf.output_stem)
            continue

        logger.info("Retrying SponsorBlock for: %s", pf.output_stem)

        # Build a minimal DownloadJob so we can reuse _retry_sponsorblock_for_job.
        minimal_meta = TrackMeta(
            title=pf.output_stem,
            artist="",
            album=None,
            album_artist=None,
            compilation=False,
            track_number=None,
            playlist_index=None,
            webpage_url=pf.source_url,
        )
        job = DownloadJob(
            key=pf.output_stem,
            output_dir=pf.audio_file.parent,
            output_stem=pf.output_stem,
            meta=minimal_meta,
            source_url=pf.source_url,
        )

        success = _retry_sponsorblock_for_job(config, job, logger, pending_file=pf)
        if success:
            succeeded.append(pf.output_stem)
        else:
            failed.append(pf.output_stem)
            append_log_line(
                config,
                "errors.log",
                f"{pf.output_stem} | SponsorBlock retry failed — sidecar kept for next attempt",
            )

    logger.info(
        "--retry-sponsorblock complete: %d succeeded, %d failed.",
        len(succeeded),
        len(failed),
    )
    if failed:
        logger.warning(
            "SponsorBlock still pending for %d file(s): %s",
            len(failed),
            ", ".join(failed),
        )


def read_playlist_url_from_m3u(m3u_path: Path) -> str | None:
    """Return the #PLAYLIST-URL: value from an M3U file, or None if not present."""
    if not m3u_path.exists():
        return None
    for line in m3u_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(_M3U_PLAYLIST_URL_PREFIX):
            url = stripped[len(_M3U_PLAYLIST_URL_PREFIX) :].strip()
            return url if url else None
    return None


def rewrite_m3u_from_dir(
    playlist_dir: Path,
    config: Config,
    logger: logging.Logger,
    playlist_url: str | None = None,
) -> None:
    if not playlist_dir.exists() or not playlist_dir.is_dir():
        raise DownloadError(f"Playlist directory not found: {playlist_dir}")
    playlist_name = playlist_dir.name
    m3u_path = playlist_dir / f"{playlist_name}.m3u"
    # Use the explicitly supplied URL (cleaned of tracking params), falling back to
    # whatever is already stored in the file.
    effective_url = (
        clean_playlist_url(playlist_url)
        if playlist_url
        else read_playlist_url_from_m3u(m3u_path)
    )
    if playlist_url and effective_url != read_playlist_url_from_m3u(m3u_path):
        logger.info("Storing playlist URL in M3U: %s", effective_url)
    files = [
        path
        for path in playlist_dir.iterdir()
        if path.is_file() and path.suffix.lower() in _AUDIO_EXTS
    ]
    files.sort(key=_track_sort_key)
    lines: list[str] = ["#EXTM3U"]
    if effective_url:
        lines.append(f"{_M3U_PLAYLIST_URL_PREFIX}{effective_url}")
    for path in files:
        artist, title = _extract_tags(path)
        extinf = f"#EXTINF:-1,{artist} - {title}"
        try:
            relative = path.relative_to(config.base_dir)
            entry = relative.as_posix()
        except ValueError:
            entry = path.as_posix()
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


def reprocess_all_playlists(config: Config, logger: logging.Logger) -> None:
    """Re-download all playlists whose M3U files contain a #PLAYLIST-URL: comment.

    This bypasses --no-overwrites and --download-archive so every track is
    re-fetched from YouTube (with SponsorBlock trimming applied), downloaded to a
    temporary directory, and then atomically moved into place only if the download
    succeeds.  The original file is preserved if the re-download fails.

    After all tracks for a playlist are swapped, compilation tags are reapplied
    via retag_playlist_dir().
    """
    if not config.base_dir.exists():
        raise DownloadError(f"Base directory not found: {config.base_dir}")

    folders = sorted(
        path
        for path in config.base_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    if not folders:
        logger.info("No playlist folders found under %s", config.base_dir)
        return

    # Collect (folder, url) pairs from M3U files.
    targets: list[tuple[Path, str]] = []
    for folder in folders:
        m3u_path = folder / f"{folder.name}.m3u"
        playlist_url = read_playlist_url_from_m3u(m3u_path)
        if playlist_url:
            targets.append((folder, playlist_url))
        else:
            logger.warning(
                "Skipping %s — M3U has no #PLAYLIST-URL: comment. "
                "Run a normal download first to store the URL.",
                folder.name,
            )

    if not targets:
        logger.info("No playlists with stored URLs found. Nothing to reprocess.")
        return

    logger.info("Reprocessing %s playlist(s)...", len(targets))

    for playlist_dir, playlist_url in targets:
        logger.info("Reprocessing playlist: %s (%s)", playlist_dir.name, playlist_url)
        _reprocess_playlist(config, playlist_dir, playlist_url, logger)

    logger.info("Reprocess complete.")


def stamp_missing_playlist_urls(config: Config, logger: logging.Logger) -> None:
    """Interactively prompt for playlist URLs for any M3U missing a #PLAYLIST-URL: stamp.

    Scans every playlist folder under config.base_dir.  For each one whose M3U
    file lacks a #PLAYLIST-URL: comment the user is shown the playlist name and
    asked to paste the URL.  Pressing Enter without typing anything skips that
    playlist.  Playlists that already have a URL stored are listed but skipped
    automatically.
    """
    if not config.base_dir.exists():
        raise DownloadError(f"Base directory not found: {config.base_dir}")

    folders = sorted(
        path
        for path in config.base_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    if not folders:
        logger.info("No playlist folders found under %s", config.base_dir)
        return

    missing: list[Path] = []
    already_stamped: list[str] = []
    for folder in folders:
        m3u_path = folder / f"{folder.name}.m3u"
        existing_url = read_playlist_url_from_m3u(m3u_path)
        if existing_url:
            already_stamped.append(folder.name)
        else:
            missing.append(folder)

    if already_stamped:
        logger.info(
            "Already stamped (%s): %s",
            len(already_stamped),
            ", ".join(already_stamped),
        )

    if not missing:
        logger.info("All playlists already have a URL stamp. Nothing to do.")
        return

    logger.info(
        "%s playlist(s) are missing a URL stamp. You will be prompted for each one.",
        len(missing),
    )
    print()

    stamped = 0
    skipped = 0
    for folder in missing:
        m3u_path = folder / f"{folder.name}.m3u"
        print(f"Playlist: {folder.name}")
        if not m3u_path.exists():
            print(f"  (no M3U file found at {m3u_path} — skipping)")
            skipped += 1
            print()
            continue
        try:
            raw = input("  Paste URL (or press Enter to skip): ").strip()
        except EOFError:
            print("\nEOF — stopping.")
            break
        if not raw:
            print("  Skipped.")
            skipped += 1
            print()
            continue
        rewrite_m3u_from_dir(
            folder, config, logger, playlist_url=clean_playlist_url(raw)
        )
        print(f"  Stamped: {clean_playlist_url(raw)}")
        stamped += 1
        print()

    logger.info("Stamp complete: %s stamped, %s skipped.", stamped, skipped)


def _reprocess_playlist(
    config: Config,
    playlist_dir: Path,
    playlist_url: str,
    logger: logging.Logger,
) -> None:
    """Re-download one playlist into a temp dir then atomically swap the files."""
    logger.info("Fetching metadata for reprocess: %s", playlist_url)
    # Disable metadata cache for reprocess so we get fresh data.
    reprocess_config = config.with_overrides(metadata_cache_enabled=False)

    try:
        info = run_yt_dlp_json(
            reprocess_config,
            playlist_url,
            logger=logger,
            extra_args=["--ignore-errors", "--flat-playlist"],
        )
    except DownloadError as exc:
        logger.error("Failed to fetch metadata for %s: %s", playlist_url, exc)
        return

    if not is_playlist(info):
        logger.warning(
            "%s did not return a playlist — skipping reprocess.", playlist_url
        )
        return

    # Build jobs but redirect output into a temp directory.
    with tempfile.TemporaryDirectory(
        prefix=f"ytdl_reprocess_{playlist_dir.name}_", dir=playlist_dir.parent
    ) as tmp_str:
        tmp_dir = Path(tmp_str)
        logger.info("Temporary download directory: %s", tmp_dir)

        # Build jobs pointing at the temp dir instead of the real playlist dir.
        jobs = _build_reprocess_jobs(reprocess_config, info, tmp_dir, logger)
        if not jobs:
            logger.warning("No jobs built for %s — skipping.", playlist_url)
            return

        # Build a reprocess-specific config variant that skips archive and overwrites.
        # We point the download archive at a throwaway path inside the temp dir so we
        # never touch (or pollute) the real archive.
        throwaway_archive = tmp_dir / "reprocess_archive.txt"
        dl_config = reprocess_config.with_overrides(
            download_archive=str(throwaway_archive),
        )

        logger.info(
            "Re-downloading %s track(s) for %s...", len(jobs), playlist_dir.name
        )
        success_count = 0
        swap_count = 0
        with ProgressReporter(total=len(jobs), logger=logger) as progress:
            with ThreadPoolExecutor(
                max_workers=dl_config.concurrent_downloads
            ) as executor:
                futures = {
                    executor.submit(
                        _reprocess_download_job, dl_config, job, logger, progress
                    ): job
                    for job in jobs
                }
                for future in as_completed(futures):
                    job = futures[future]
                    exc = future.exception()
                    if exc:
                        logger.error(
                            "Reprocess download failed for %s: %s", job.output_stem, exc
                        )
                    else:
                        success_count += 1

        # Atomically move successful downloads into the real playlist dir.
        for job in jobs:
            tmp_file = tmp_dir / job.output_filename(dl_config)
            if not tmp_file.exists():
                logger.warning(
                    "Temp file missing after reprocess — keeping original: %s",
                    job.output_stem,
                )
                continue
            dest = playlist_dir / job.output_filename(config)
            try:
                shutil.move(str(tmp_file), str(dest))
                swap_count += 1
                logger.info("Swapped: %s", dest.name)
            except OSError as exc:
                logger.error("Failed to move %s → %s: %s", tmp_file, dest, exc)

        logger.info(
            "Reprocess %s: %s/%s downloaded, %s swapped into place.",
            playlist_dir.name,
            success_count,
            len(jobs),
            swap_count,
        )

    # Re-apply compilation/Navidrome tags after the swap as a safety net —
    # belt-and-suspenders in case any file was moved before its tags were written.
    # Always run this if anything was swapped, not just when all succeeded.
    if swap_count:
        logger.info("Re-applying compilation tags for %s...", playlist_dir.name)
        retag_playlist_dir(playlist_dir, config, logger)

    # Rewrite the M3U so it reflects the current state (preserving the URL comment).
    rewrite_m3u_from_dir(playlist_dir, config, logger)


def _build_reprocess_jobs(
    config: Config, info: dict, tmp_dir: Path, logger: logging.Logger
) -> list[DownloadJob]:
    """Build DownloadJobs for a reprocess run, redirecting output to tmp_dir."""
    entries = [entry for entry in info.get("entries") or [] if entry]
    if not entries:
        return []
    total = len(entries)
    playlist_title = sanitize(info.get("title") or "playlist")
    width = max(2, len(str(total)))
    jobs: list[DownloadJob] = []
    for index, entry in enumerate(entries, start=1):
        entry_url = entry.get("url") or entry.get("id")
        if entry_url and not str(entry_url).startswith("http"):
            entry_url = f"https://music.youtube.com/watch?v={entry_url}"
        if not entry_url:
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
                logger.warning("Skipping unavailable entry during reprocess: %s", exc)
                continue
        if not meta_info or not meta_info.get("title"):
            continue
        playlist_index = safe_int(entry.get("playlist_index"), default=index)
        meta = build_track_meta(
            meta_info,
            playlist_index,
            playlist_title=playlist_title,
            is_compilation=True,
        )
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
                output_dir=tmp_dir,
                output_stem=stem,
                meta=meta,
                source_url=str(source_url),
                m3u_path=None,  # No M3U during reprocess; we rewrite after.
            )
        )
    return jobs


def _extract_failure_reason(last_lines: "deque[str]", returncode: int) -> str:
    """Return a concise failure reason from the last yt-dlp output lines.

    Prefers lines that look like errors (contain 'ERROR', 'error', 'HTTP',
    '429', 'Sign in', etc.) over generic progress lines.  Falls back to the
    last non-empty line, and ultimately to just the exit code.
    """
    _ERROR_KEYWORDS = (
        "ERROR",
        "error:",
        "WARNING",
        "429",
        "HTTP Error",
        "Sign in",
        "unavailable",
        "blocked",
        "forbidden",
        "private",
        "removed",
    )
    # Scan in reverse so we favour the most recent relevant line.
    for line in reversed(list(last_lines)):
        stripped = line.strip()
        if stripped and any(kw in stripped for kw in _ERROR_KEYWORDS):
            return stripped
    # Fall back to the last non-empty line.
    for line in reversed(list(last_lines)):
        stripped = line.strip()
        if stripped:
            return stripped
    return f"exit code {returncode}"


def _is_sponsorblock_api_error(last_lines: "deque[str]") -> bool:
    """Return True if the yt-dlp output indicates a SponsorBlock API failure.

    We look for yt-dlp's standard phrase for SponsorBlock connectivity problems.
    This distinguishes a transient external-service error (file still downloaded
    successfully) from a genuine download failure.
    """
    return any(_SPONSORBLOCK_API_ERROR_PHRASE in line for line in last_lines)


def _retry_sponsorblock_for_job(
    config: Config,
    job: DownloadJob,
    logger: logging.Logger,
    attempts: int = _SPONSORBLOCK_RETRY_ATTEMPTS,
    pending_file: "PendingFile | None" = None,
) -> bool:
    """Apply SponsorBlock segment removal locally for an already-downloaded file.

    Calls the SponsorBlock REST API directly to fetch segment timestamps, then
    uses ffmpeg to cut/mute those segments from the local audio file.  No
    re-download from YouTube is performed.

    Flow:
    1. Extract the video ID from ``job.source_url``.
    2. Query the SponsorBlock API.
    3. If the API returns a transient error: return False (keep sidecar).
    4. If the API returns no segments (404): clear sidecar, write resolved
       marker to errors.log, return True.
    5. If segments are found: run ffmpeg to remove them, clear sidecar,
       return True.

    If *pending_file* is supplied and processing succeeds, the
    ``sponsorblock`` task is removed from it (and the sidecar deleted if no
    tasks remain).

    Returns True if the track is now clean, False on transient failure.
    """
    import urllib.error as _uerr

    source_url = job.source_url or job.meta.webpage_url

    # ── Step 1: Extract video ID ────────────────────────────────────────────
    video_id = extract_video_id(source_url) if source_url else None
    if not video_id:
        logger.warning(
            "SponsorBlock retry: cannot extract video ID from URL %r — skipping %s",
            source_url,
            job.output_stem,
        )
        return False

    # ── Step 2: Query SponsorBlock API ──────────────────────────────────────
    categories = config.sponsorblock_categories or ()
    segments = None
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(config.sleep_interval)
        try:
            segments = fetch_segments(video_id, categories, logger=logger)
            break  # success (including 404 → empty list)
        except _uerr.HTTPError as exc:
            logger.warning(
                "SponsorBlock API error (attempt %d/%d) for %s: HTTP %d",
                attempt,
                attempts,
                job.output_stem,
                exc.code,
            )
        except OSError as exc:
            logger.warning(
                "SponsorBlock API unreachable (attempt %d/%d) for %s: %s",
                attempt,
                attempts,
                job.output_stem,
                exc,
            )

    if segments is None:
        # All attempts exhausted — transient failure.
        logger.warning(
            "SponsorBlock retry failed for %s after %d attempt(s) — API unreachable.",
            job.output_stem,
            attempts,
        )
        return False

    # ── Step 3: No segments found ───────────────────────────────────────────
    if not segments:
        logger.info(
            "SponsorBlock: no segments in database for %s — file is already clean.",
            job.output_stem,
        )
        if pending_file is not None:
            pending_file.remove_task(PENDING_TASK_SPONSORBLOCK)
        # Write a resolved marker so bootstrap never re-creates a sidecar for
        # this stem — it is permanently clean as far as SponsorBlock is concerned.
        append_log_line(
            config,
            "errors.log",
            f"{job.output_stem} | SponsorBlock resolved — no segments in database",
        )
        return True

    # ── Step 4: Remove segments with ffmpeg ─────────────────────────────────
    audio_file = find_existing_file(job.output_dir, job.output_stem)
    if audio_file is None:
        logger.warning(
            "SponsorBlock retry: audio file for %s not found in %s.",
            job.output_stem,
            job.output_dir,
        )
        return False

    try:
        remove_segments_ffmpeg(
            audio_file,
            segments,
            ffmpeg_bin=config.ffmpeg_bin,
            logger=logger,
        )
    except RuntimeError as exc:
        logger.warning(
            "ffmpeg segment removal failed for %s: %s",
            job.output_stem,
            exc,
        )
        return False

    logger.info(
        "SponsorBlock applied successfully for %s (%d segment(s) removed).",
        job.output_stem,
        len(segments),
    )
    if pending_file is not None:
        pending_file.remove_task(PENDING_TASK_SPONSORBLOCK)
    return True


def _reprocess_download_job(
    config: Config,
    job: DownloadJob,
    logger: logging.Logger,
    progress: ProgressReporter,
) -> None:
    """Like download_job() but without --no-overwrites, for reprocess mode."""
    source_url = job.source_url or job.meta.webpage_url
    job.output_dir.mkdir(parents=True, exist_ok=True)

    # Build args without --no-overwrites so existing files are replaced.
    args = _yt_dlp_args_reprocess(config, job)
    args.append(source_url)
    progress.add_task(job.key, job.output_stem, total=100)

    reason = "unknown error"
    for attempt in range(1, config.retries + 1):
        logger.debug(
            "Reprocess downloading %s (attempt %s/%s)",
            job.output_stem,
            attempt,
            config.retries,
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
            line = line.rstrip()
            last_lines.append(line)
            match = _PROGRESS_RE.search(line)
            if match:
                pct = float(match.group(1))
                progress.update(job.key, completed=pct)
        process.wait()
        if process.returncode == 0:
            # Apply ALBUMARTIST/COMPILATION tags immediately while the file is
            # still in the temp dir. Without this, yt-dlp's embedded per-track
            # artist tags are what Navidrome reads, causing ghost albums.
            downloaded = find_existing_file(job.output_dir, job.output_stem)
            if downloaded and (job.meta.compilation or job.meta.album_artist):
                apply_compilation_tags(downloaded, job.meta, logger)
            progress.complete(job.key)
            return
        # Extract the most informative line from yt-dlp output for the error
        # message — skip blank lines and prefer lines that look like errors.
        reason = _extract_failure_reason(last_lines, process.returncode)
        logger.debug(
            "Reprocess attempt %s/%s failed for %s: %s",
            attempt,
            config.retries,
            job.output_stem,
            reason,
        )
        if attempt < config.retries:
            time.sleep(config.sleep_interval)

    progress.advance_overall()
    raise DownloadError(
        f"Reprocess download failed after {config.retries} attempt(s): "
        f"{job.output_stem} — {reason}"
    )


def _yt_dlp_args_reprocess(
    config: Config,
    job: DownloadJob,
    output_template: str | None = None,
) -> list[str]:
    """Build yt-dlp args for reprocess mode: no --no-overwrites, throwaway archive.

    *output_template* overrides job.output_template when supplied — used by the
    SponsorBlock retry to redirect output to a temp dir so yt-dlp temp files
    don't clobber the real audio file or its sidecar.

    Intentionally omits --embed-thumbnail / --add-metadata because those require
    AtomicParsley or specific ffmpeg builds and are not needed for SponsorBlock
    segment removal.  The existing embedded tags and thumbnail are preserved as-is.
    """
    effective_template = (
        output_template if output_template is not None else job.output_template
    )
    args = [
        config.yt_dlp_bin,
        "--newline",
        "--continue",
        # NOTE: --no-overwrites intentionally omitted so files are replaced.
        "--extract-audio",
        "--audio-format",
        config.audio_format,
        "--embed-metadata",
        # --embed-thumbnail and --add-metadata intentionally omitted — they are
        # not needed for SponsorBlock segment removal and can cause
        # "Conversion failed!" errors when AtomicParsley is unavailable.
        "-f",
        "bestaudio",
        "--download-archive",
        str(config.download_archive),  # Points at the throwaway archive in tmp_dir.
        "-o",
        effective_template,
    ]
    if config.rate_limit:
        args += ["--rate-limit", config.rate_limit]
    args += ["--sleep-interval", str(config.sleep_interval)]
    args += ["--max-sleep-interval", str(config.max_sleep_interval)]
    args += ["--retries", str(config.retries)]
    if config.cookies_path.exists():
        args += ["--cookies", str(config.cookies_path)]
    if config.sponsorblock_categories:
        args += ["--sponsorblock-remove", ",".join(config.sponsorblock_categories)]
    return args


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
