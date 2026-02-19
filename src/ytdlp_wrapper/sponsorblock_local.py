"""Local SponsorBlock integration: API queries and ffmpeg-based segment removal.

This module provides a fully local approach to SponsorBlock post-processing:

1. ``fetch_segments`` — queries the SponsorBlock REST API directly (no yt-dlp).
2. ``remove_segments_ffmpeg`` — cuts or mutes sponsor segments from a local
   audio file using ffmpeg, writing the result atomically over the original.
3. ``extract_video_id`` — pulls the ``v=`` parameter from a YouTube URL.

No new dependencies are introduced; only the stdlib (``urllib``, ``json``,
``subprocess``, ``shutil``, ``pathlib``) and the project's existing ``ffmpeg``
binary from ``Config`` are used.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: SponsorBlock categories that require an audio cut (segment is removed).
ACTION_SKIP = "skip"

#: SponsorBlock categories that require only a mute (silence, no cut).
ACTION_MUTE = "mute"

#: The base URL of the SponsorBlock API.
SPONSORBLOCK_API_BASE = "https://sponsor.ajay.app/api/skipSegments"

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

#: A single sponsor segment: (start_seconds, end_seconds, action_type).
Segment = tuple[float, float, str]


# ---------------------------------------------------------------------------
# Video ID extraction
# ---------------------------------------------------------------------------


def extract_video_id(url: str) -> str | None:
    """Return the YouTube video ID from *url*, or ``None`` if not found.

    Handles both ``?v=ID`` query-param style and ``youtu.be/ID`` short links.

    >>> extract_video_id("https://music.youtube.com/watch?v=YqivYZYykSo")
    'YqivYZYykSo'
    >>> extract_video_id("https://youtu.be/YqivYZYykSo")
    'YqivYZYykSo'
    >>> extract_video_id("https://example.com/no-id-here")
    """
    parsed = urllib.parse.urlparse(url)
    # Standard watch URL: ?v=ID
    params = urllib.parse.parse_qs(parsed.query)
    if "v" in params and params["v"]:
        return params["v"][0]
    # Short link: youtu.be/ID or path-based
    path = parsed.path.lstrip("/")
    if path:
        return path.split("/")[0]
    return None


# ---------------------------------------------------------------------------
# SponsorBlock API
# ---------------------------------------------------------------------------


def fetch_segments(
    video_id: str,
    categories: tuple[str, ...] | list[str],
    *,
    timeout: int = 10,
    logger: logging.Logger | None = None,
) -> list[Segment]:
    """Query the SponsorBlock API for skip/mute segments for *video_id*.

    Only ``actionType`` values of ``"skip"`` and ``"mute"`` are returned;
    ``"chapter"``, ``"poi"``, and ``"full"`` are ignored (they are not
    relevant for audio-only trimming).

    Parameters
    ----------
    video_id:
        The YouTube video ID (e.g. ``"YqivYZYykSo"``).
    categories:
        Iterable of SponsorBlock category strings, e.g.
        ``("sponsor", "selfpromo", "music_offtopic")``.
    timeout:
        HTTP request timeout in seconds.

    Returns
    -------
    list[Segment]
        Sorted list of ``(start, end, action_type)`` tuples.  Empty when the
        API returns 404 (no segments for this video) or when no segments match
        the requested categories.

    Raises
    ------
    urllib.error.HTTPError
        For any non-404 HTTP error (e.g. 500, 503) — the caller should treat
        this as a transient failure and keep the sidecar for a later retry.
    OSError
        For network errors (connection refused, timeout, etc.).
    """
    params = urllib.parse.urlencode(
        {
            "videoID": video_id,
            "categories": json.dumps(list(categories)),
        }
    )
    url = f"{SPONSORBLOCK_API_BASE}?{params}"
    if logger:
        logger.debug("SponsorBlock API request: %s", url)

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # 404 = no segments found for those categories — not an error.
            if logger:
                logger.debug("SponsorBlock: no segments for video %s (404)", video_id)
            return []
        # Any other HTTP error is a transient server problem.
        raise

    segments: list[Segment] = []
    for item in data:
        action = item.get("actionType", "")
        if action not in (ACTION_SKIP, ACTION_MUTE):
            continue
        seg = item.get("segment", [])
        if len(seg) < 2:
            continue
        try:
            start = float(seg[0])
            end = float(seg[1])
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        segments.append((start, end, action))

    # Sort by start time so the ffmpeg filter is deterministic.
    segments.sort(key=lambda s: s[0])
    if logger and segments:
        logger.debug(
            "SponsorBlock: found %d segment(s) for video %s: %s",
            len(segments),
            video_id,
            [(f"{s:.2f}", f"{e:.2f}", a) for s, e, a in segments],
        )
    return segments


# ---------------------------------------------------------------------------
# ffmpeg segment removal
# ---------------------------------------------------------------------------


def remove_segments_ffmpeg(
    audio_file: Path,
    segments: list[Segment],
    ffmpeg_bin: str = "ffmpeg",
    *,
    logger: logging.Logger | None = None,
) -> None:
    """Remove or mute sponsor segments from *audio_file* using ffmpeg.

    The operation is atomic: ffmpeg writes to a temporary file in the same
    directory, then the original is replaced only if ffmpeg exits cleanly.

    Parameters
    ----------
    audio_file:
        Path to the existing audio file (e.g. ``/media/music/…/048-Foo.opus``).
    segments:
        List of ``(start, end, action_type)`` tuples from :func:`fetch_segments`.
        ``"skip"`` segments are cut out; ``"mute"`` segments are silenced.
        If empty, the function is a no-op.
    ffmpeg_bin:
        Name or path of the ffmpeg executable.

    Raises
    ------
    RuntimeError
        If ffmpeg exits with a non-zero code.
    """
    if not segments:
        return

    skip_segs = [(s, e) for s, e, a in segments if a == ACTION_SKIP]
    mute_segs = [(s, e) for s, e, a in segments if a == ACTION_MUTE]

    filters: list[str] = []

    # ── Cut (skip) filter ───────────────────────────────────────────────────
    # Keep every moment that is NOT inside any skip segment.
    # aselect keeps samples where the expression is non-zero.
    # asetpts resets timestamps so the output is gapless.
    if skip_segs:
        # Build: between(t,s1,e1)+between(t,s2,e2)+... == 0
        between_exprs = "+".join(f"between(t,{s},{e})" for s, e in skip_segs)
        filters.append(f"aselect='{between_exprs}==0',asetpts=N/SR/TB")

    # ── Mute filter ─────────────────────────────────────────────────────────
    # For each mute segment, use volume=0 during that time range.
    for s, e in mute_segs:
        # volume filter with enable= time range
        filters.append(f"volume=0:enable='between(t,{s},{e})'")

    filter_str = ",".join(filters)

    suffix = audio_file.suffix
    # Write to a temp file alongside the original so the move is on the same
    # filesystem (atomic on POSIX; near-atomic on Windows via shutil.move).
    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(suffix=f".tmp{suffix}", dir=audio_file.parent)
        import os

        os.close(fd)
        tmp_path = Path(tmp_str)

        args = [
            ffmpeg_bin,
            "-y",  # overwrite the temp file we just created
            "-i",
            str(audio_file),
            "-af",
            filter_str,
            "-vn",  # drop any attached video/cover streams
            str(tmp_path),
        ]
        if logger:
            logger.debug("ffmpeg segment removal: %s", " ".join(args))

        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            err = result.stderr.strip().splitlines()
            detail = err[-1] if err else "(no output)"
            raise RuntimeError(
                f"ffmpeg exited {result.returncode} while processing "
                f"{audio_file.name}: {detail}"
            )

        shutil.move(str(tmp_path), str(audio_file))
        tmp_path = None  # ownership transferred — don't delete in finally
        if logger:
            logger.info(
                "ffmpeg: removed %d segment(s) from %s",
                len(segments),
                audio_file.name,
            )
    finally:
        # Clean up the temp file if something went wrong before the move.
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
