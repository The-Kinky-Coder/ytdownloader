"""Audio normalization helpers copied from normalize_music.py.

This module provides library functions for two-pass EBU R128 loudness
normalization using ffmpeg. It is intended to be used internally by the
wrapper; the original standalone script is left untouched as a reference.

The key feature is that files are tagged with an easy mutagen tag named
"normalized" so subsequent invocations can skip already-processed files.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from mutagen import File as MutagenFile

# Configuration defaults (consumer may override by setting constants or
# passing parameters to the main normalization function).
TARGET_LUFS = -14.0
TRUE_PEAK = -1.0
LRA = 11.0

CODEC_MAP = {
    ".mp3": {"codec": "libmp3lame", "extra": ["-q:a", "2"]},
    ".flac": {"codec": "flac", "extra": []},
    ".m4a": {"codec": "aac", "extra": ["-b:a", "256k"]},
    ".aac": {"codec": "aac", "extra": ["-b:a", "256k"]},
    ".ogg": {"codec": "libvorbis", "extra": ["-q:a", "6"]},
    ".opus": {"codec": "libopus", "extra": ["-b:a", "128k"]},
}
SUPPORTED_EXTENSIONS = set(CODEC_MAP.keys())
NORMALIZED_TAG = "normalized"
NORMALIZED_VALUE = "1"


def _run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
    )


def configure_mutagen_keys() -> None:
    """Ensure easy tag key is registered across formats."""
    try:
        # registration may throw if already present
        from mutagen.easyid3 import EasyID3

        EasyID3.RegisterTXXXKey(NORMALIZED_TAG, NORMALIZED_TAG)
    except Exception:  # pragma: no cover - idempotent
        pass
    try:
        from mutagen.easymp4 import EasyMP4Tags

        EasyMP4Tags.RegisterFreeformKey(NORMALIZED_TAG, NORMALIZED_TAG)
    except Exception:  # pragma: no cover
        pass


def is_normalized(path: Path) -> bool:
    """Return True if *path* already has the normalization marker tag."""
    try:
        configure_mutagen_keys()
        audio = MutagenFile(path, easy=True)
        if not audio or not audio.tags:
            return False
        return bool(audio.tags.get(NORMALIZED_TAG))
    except Exception:
        # treat failure as "not normalized" so that an error doesn't hide a
        # valid file
        return False


def mark_normalized(path: Path) -> bool:
    """Add the normalized tag to *path*. Returns True on success."""
    try:
        configure_mutagen_keys()
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return False
        if audio.tags is None:
            audio.add_tags()
        audio.tags[NORMALIZED_TAG] = [NORMALIZED_VALUE]
        audio.save()
        return True
    except Exception:
        return False


def measure_loudness(input_path: Path) -> dict:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(input_path),
        "-af",
        f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA={LRA}:print_format=json",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    stderr = result.stderr.decode("utf-8", errors="replace")
    start = stderr.rfind("{")
    end = stderr.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No loudnorm JSON found in ffmpeg output for: {input_path}")
    return json.loads(stderr[start:end])


def normalize_file(input_path: Path) -> bool:
    """Perform two-pass loudness normalization on *input_path*.

    Returns True on successful normalization and tagging, False otherwise.
    Skips the file if it is already normalized.
    """
    # shortcut: unsupported extension should never be considered normalized
    ext = input_path.suffix.lower()
    if ext not in CODEC_MAP:
        return False
    if is_normalized(input_path):
        return True
    codec_cfg = CODEC_MAP[ext]

    # pass 1
    stats = measure_loudness(input_path)
    loudnorm_filter = (
        f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA={LRA}"
        f":measured_I={stats['input_i']}"
        f":measured_TP={stats['input_tp']}"
        f":measured_LRA={stats['input_lra']}"
        f":measured_thresh={stats['input_thresh']}"
        f":offset={stats['target_offset']}"
        f":linear=true:print_format=none"
    )

    tmp_dir = input_path.parent
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext, dir=str(tmp_dir))
    os.close(tmp_fd)
    try:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(input_path),
            "-af",
            loudnorm_filter,
            "-map_metadata",
            "0",
            "-map",
            "0:a:0",
            "-c:a",
            codec_cfg["codec"],
            *codec_cfg["extra"],
            tmp_path,
        ]
        _run(cmd)
        if os.path.getsize(tmp_path) < 1024:
            raise ValueError("Output file is suspiciously small")
        shutil.move(tmp_path, str(input_path))
        mark_normalized(input_path)
        return True
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False


def _normalize_worker(p: Path) -> tuple[Path, bool]:
    """Picklable helper for multiprocessing pools."""
    return (p, normalize_file(p))


def normalize_dir(
    root: Path,
    workers: int = 2,
    target_lufs: float | None = None,
    logger: logging.Logger | None = None,
    progress: object | None = None,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Recursively normalize files under *root*.

    Returns a tuple ``(success_count, failure_count)``.
    If *target_lufs* is provided it overrides the module-level constant.

    *progress* may be a :class:`ProgressReporter`-like object with
    ``add_task(key, label)`` and ``complete(key)`` methods; if provided it
    will be used to display per-file progress.  This mirrors the behaviour of
    :func:`normalize_files`.
    """
    if target_lufs is not None:
        global TARGET_LUFS
        TARGET_LUFS = target_lufs
    initialize = logger or logging.getLogger(__name__)
    files: list[Path] = []
    for dirpath, _, filenames in os.walk(root):
        for fname in sorted(filenames):
            path = Path(dirpath) / fname
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(path)
    to_process = [p for p in files if not is_normalized(p)]
    if dry_run:
        for p in to_process:
            print(p)
        return (0, 0)
    success = 0
    failed = 0
    # register progress tasks up front if requested
    if progress is not None:
        for p in to_process:
            progress.add_task(str(p), p.name)
    if workers <= 1:
        for p in to_process:
            result = normalize_file(p)
            if progress is not None:
                progress.complete(str(p))
            if result:
                success += 1
            else:
                failed += 1
                initialize.warning("Normalization failed: %s", p)
        return (success, failed)
    # if more than one worker, execute in parallel
    import multiprocessing
    with multiprocessing.Pool(processes=workers) as pool:
        for p, result in pool.imap_unordered(_normalize_worker, to_process):
            if progress is not None:
                progress.complete(str(p))
            if result:
                success += 1
            else:
                failed += 1
                initialize.warning("Normalization failed: %s", p)
    return (success, failed)


def normalize_files(
    files: Iterable[Path],
    workers: int = 2,
    target_lufs: float | None = None,
    logger: logging.Logger | None = None,
    progress: object | None = None,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Normalize a given iterable of audio file paths.

    Skips paths that already have the normalized tag.  Returns
    ``(success_count, failure_count)``.

    *progress* may be a :class:`ProgressReporter`-like object with
    ``add_task(key, label)`` and ``complete(key)`` methods.  If supplied, it
    will be used to display a per-file progress bar during the normalization
    run (similar to how download progress is reported).
    """
    if target_lufs is not None:
        global TARGET_LUFS
        TARGET_LUFS = target_lufs
    initialize = logger or logging.getLogger(__name__)
    paths = [p for p in files if p.suffix.lower() in SUPPORTED_EXTENSIONS]
    to_process = [p for p in paths if not is_normalized(p)]
    if dry_run:
        for p in to_process:
            print(p)
        return (0, 0)
    if not to_process:
        return (0, 0)
    success = 0
    failed = 0
    # register progress tasks if requested
    if progress is not None:
        for p in to_process:
            progress.add_task(str(p), p.name)
    if workers <= 1:
        for p in to_process:
            result = normalize_file(p)
            if progress is not None:
                progress.complete(str(p))
            if result:
                success += 1
            else:
                failed += 1
                initialize.warning("Normalization failed: %s", p)
        return (success, failed)
    import multiprocessing
    with multiprocessing.Pool(processes=workers) as pool:
        for p, result in pool.imap_unordered(_normalize_worker, to_process):
            if progress is not None:
                progress.complete(str(p))
            if result:
                success += 1
            else:
                failed += 1
                initialize.warning("Normalization failed: %s", p)
    return (success, failed)


def check_ffmpeg() -> bool:
    """Return True if ffmpeg is available on PATH."""
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False
