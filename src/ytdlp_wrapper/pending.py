"""Sidecar `.pending.json` files for deferred post-processing.

When a download completes but a post-processing step fails (e.g. the
SponsorBlock API was unreachable), we write a small JSON sidecar alongside
the audio file recording what still needs to be done.  On a later run the
user can invoke ``--retry-sponsorblock`` (or a future general
``--process-pending``) to pick up where we left off without having to
re-download anything.

Sidecar filename convention::

    <audio-stem>.pending.json

e.g. ``048-Sleepermane & Aylior - Topic-Pebbles.pending.json``

File format (version 1)::

    {
        "version": 1,
        "source_url": "https://music.youtube.com/watch?v=abc123",
        "output_stem": "048-Sleepermane & Aylior - Topic-Pebbles",
        "pending": ["sponsorblock"],
        "created": "2026-02-19T14:32:01"
    }

``pending`` is a list of string task tokens.  Currently only
``"sponsorblock"`` is defined.  Tasks are removed from the list as they
succeed; the sidecar file is deleted once the list is empty.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PENDING_TASK_SPONSORBLOCK = "sponsorblock"

# Sidecar files use this suffix so they are easy to glob and are clearly not
# audio files (Navidrome and similar media servers ignore them).
_SIDECAR_SUFFIX = ".pending.json"

_FORMAT_VERSION = 1


@dataclass
class PendingFile:
    """In-memory representation of a ``.pending.json`` sidecar."""

    source_url: str
    output_stem: str
    audio_file: Path  # absolute path to the corresponding audio file
    pending: list[str] = field(default_factory=list)
    created: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def sidecar_path(self) -> Path:
        return audio_file_to_sidecar(self.audio_file)

    def has_task(self, task: str) -> bool:
        return task in self.pending

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Write (or overwrite) the sidecar file on disk."""
        payload = {
            "version": _FORMAT_VERSION,
            "source_url": self.source_url,
            "output_stem": self.output_stem,
            "pending": self.pending,
            "created": self.created,
        }
        self.sidecar_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def remove_task(self, task: str) -> None:
        """Mark *task* as done.  Deletes the sidecar if no tasks remain."""
        try:
            self.pending.remove(task)
        except ValueError:
            pass
        if not self.pending:
            self.delete()
        else:
            self.save()

    def delete(self) -> None:
        """Delete the sidecar file if it exists."""
        try:
            self.sidecar_path.unlink()
        except FileNotFoundError:
            pass


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def audio_file_to_sidecar(audio_file: Path) -> Path:
    """Return the sidecar path for the given audio file path.

    >>> audio_file_to_sidecar(Path("/music/Playlist/001-Artist-Song.opus"))
    PosixPath('/music/Playlist/001-Artist-Song.pending.json')
    """
    return audio_file.with_suffix(_SIDECAR_SUFFIX)


def sidecar_path_for_stem(output_dir: Path, output_stem: str) -> Path:
    """Return the sidecar path given a directory and stem (no extension)."""
    return output_dir / f"{output_stem}{_SIDECAR_SUFFIX}"


def write_pending(
    audio_file: Path,
    source_url: str,
    output_stem: str,
    tasks: list[str],
    logger: logging.Logger | None = None,
) -> PendingFile:
    """Create or update a sidecar for *audio_file*.

    If the sidecar already exists its ``pending`` list is merged with *tasks*
    (no duplicates).  The ``source_url`` and ``created`` timestamp are
    preserved from the existing file when merging.
    """
    sidecar = audio_file_to_sidecar(audio_file)
    if sidecar.exists():
        existing = _load_sidecar(sidecar, audio_file)
        if existing is not None:
            for t in tasks:
                if t not in existing.pending:
                    existing.pending.append(t)
            existing.save()
            if logger:
                logger.debug(
                    "Updated sidecar %s — pending: %s", sidecar.name, existing.pending
                )
            return existing

    pf = PendingFile(
        source_url=source_url,
        output_stem=output_stem,
        audio_file=audio_file,
        pending=list(tasks),
    )
    pf.save()
    if logger:
        logger.debug("Created sidecar %s — pending: %s", sidecar.name, tasks)
    return pf


def find_pending_sidecars(
    base_dir: Path,
    task: str | None = None,
    logger: logging.Logger | None = None,
) -> list[PendingFile]:
    """Recursively find all sidecar files under *base_dir*.

    If *task* is given, only return sidecars that contain that task token.
    Sidecars that cannot be parsed are logged and skipped.
    """
    results: list[PendingFile] = []
    for sidecar in sorted(base_dir.rglob(f"*{_SIDECAR_SUFFIX}")):
        # Skip yt-dlp temporary files (e.g. foo.temp.pending.json) — these are
        # in-progress download artifacts left behind on interrupted runs.
        if ".temp." in sidecar.name:
            if logger:
                logger.debug("Skipping temporary sidecar artifact: %s", sidecar.name)
            continue
        # Derive the audio file path by replacing the sidecar suffix with an
        # empty suffix — we don't know the audio extension ahead of time, so
        # we glob for it.
        stem = sidecar.name[: -len(_SIDECAR_SUFFIX)]
        audio_candidates = list(sidecar.parent.glob(f"{stem}.*"))
        # Exclude the sidecar itself from the candidates.
        audio_candidates = [
            p for p in audio_candidates if not p.name.endswith(_SIDECAR_SUFFIX)
        ]
        if not audio_candidates:
            if logger:
                logger.warning(
                    "Sidecar %s has no matching audio file — skipping.", sidecar
                )
            continue
        audio_file = audio_candidates[0]
        pf = _load_sidecar(sidecar, audio_file, logger=logger)
        if pf is None:
            continue
        if task is None or pf.has_task(task):
            results.append(pf)
    return results


def _load_sidecar(
    sidecar: Path,
    audio_file: Path,
    logger: logging.Logger | None = None,
) -> PendingFile | None:
    """Parse a sidecar JSON file.  Returns None on any parse error."""
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if logger:
            logger.warning("Could not read sidecar %s: %s", sidecar, exc)
        return None
    if not isinstance(data, dict):
        if logger:
            logger.warning("Sidecar %s has unexpected format — skipping.", sidecar)
        return None
    return PendingFile(
        source_url=data.get("source_url", ""),
        output_stem=data.get("output_stem", audio_file.stem),
        audio_file=audio_file,
        pending=data.get("pending", []),
        created=data.get("created", ""),
    )
