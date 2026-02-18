"""Progress reporting with rich when available."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any


try:  # pragma: no cover - optional dependency
    import rich  # noqa: F401

    _RICH_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _RICH_AVAILABLE = False


@dataclass
class DownloadTask:
    task_id: Any
    label: str


class ProgressReporter:
    """Reports download progress.

    Displays a single overall progress bar (N/total tracks) rather than one
    row per track.  Per-track completion is logged as a plain line so the
    display stays stable and never flashes or overflows the terminal.

    Rich's auto-refresh is disabled; the bar is only redrawn on meaningful
    events (a track completes or the overall count advances) to eliminate the
    constant flicker caused by rapid per-byte update calls from concurrent
    download threads.

    On entry the logging ``StreamHandler`` named ``"stream"`` is replaced with
    a ``RichHandler`` that routes all log output through the same console,
    preventing interleaving between logger output and the progress bar.  The
    original handler is restored on exit.
    """

    def __init__(self, total: int, logger: logging.Logger) -> None:
        self._logger = logger
        self._total = total
        self._completed = 0
        self._tasks: dict[str, DownloadTask] = {}
        self._use_rich = _RICH_AVAILABLE
        self._progress: Any = None
        self._overall_task: Any = None
        self.console: Any = None  # exposed for callers that need the console

        # Saved state for handler swapping
        self._stream_handler: logging.Handler | None = None
        self._rich_handler: Any = None

        if self._use_rich:
            from rich.console import Console
            from rich.progress import (
                BarColumn,
                MofNCompleteColumn,
                Progress,
                SpinnerColumn,
                TextColumn,
                TimeElapsedColumn,
                TimeRemainingColumn,
            )

            self.console = Console()
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]Downloading"),
                BarColumn(bar_width=40),
                MofNCompleteColumn(),
                TextColumn("tracks"),
                TimeElapsedColumn(),
                TextColumn("elapsed /"),
                TimeRemainingColumn(),
                TextColumn("remaining"),
                # Disable auto-refresh: we refresh manually only when something
                # actually changes, preventing the constant flicker.
                auto_refresh=False,
                transient=False,
                console=self.console,
            )
            self._overall_task = self._progress.add_task(
                "overall",
                total=total,
                label="Overall",
            )

    # ------------------------------------------------------------------
    # Rich handler swapping helpers
    # ------------------------------------------------------------------

    def _install_rich_handler(self) -> None:
        """Replace the 'stream' StreamHandler with a RichHandler."""
        if not self._use_rich or self.console is None:
            return
        try:
            from rich.logging import RichHandler
        except Exception:
            return

        root = self._logger
        # Walk up to find the handler named "stream" on any ancestor logger.
        target_logger: logging.Logger | None = None
        handler_to_swap: logging.Handler | None = None
        candidate = root
        while candidate is not None:
            for h in candidate.handlers:
                if h.get_name() == "stream":
                    target_logger = candidate
                    handler_to_swap = h
                    break
            if handler_to_swap:
                break
            if not candidate.propagate:
                break
            candidate = candidate.parent  # type: ignore[assignment]

        if handler_to_swap is None or target_logger is None:
            return

        rich_handler = RichHandler(
            console=self.console,
            show_time=False,
            show_path=False,
            markup=False,
        )
        rich_handler.setLevel(handler_to_swap.level)
        rich_handler.set_name("stream_rich")

        target_logger.removeHandler(handler_to_swap)
        target_logger.addHandler(rich_handler)

        self._stream_handler = handler_to_swap
        self._rich_handler = rich_handler
        self._target_logger = target_logger

    def _restore_stream_handler(self) -> None:
        """Restore the original StreamHandler, removing the RichHandler."""
        target_logger: logging.Logger | None = getattr(self, "_target_logger", None)
        if target_logger is None:
            return
        if self._rich_handler is not None:
            target_logger.removeHandler(self._rich_handler)
            self._rich_handler = None
        if self._stream_handler is not None:
            target_logger.addHandler(self._stream_handler)
            self._stream_handler = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "ProgressReporter":
        if self._use_rich and self._progress is not None:
            self._progress.start()
        self._install_rich_handler()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._restore_stream_handler()
        if self._use_rich and self._progress is not None:
            self._progress.stop()

    # ------------------------------------------------------------------
    # Progress API
    # ------------------------------------------------------------------

    def add_task(self, key: str, label: str, total: int | None = None) -> None:
        # Per-track tasks are tracked internally but not rendered as individual
        # rows — only the overall bar is shown on screen.
        if self._use_rich:
            self._tasks[key] = DownloadTask(task_id=None, label=label)
        else:
            self._logger.info("Starting: %s", label)

    def update(
        self, key: str, completed: float | None = None, total: float | None = None
    ) -> None:
        # Per-track percentage updates are intentionally ignored for rendering.
        # The overall bar only moves when a track finishes (see complete /
        # advance_overall), so there is nothing to redraw here.
        pass

    def complete(self, key: str) -> None:
        self._completed += 1
        task = self._tasks.get(key)
        label = task.label if task else key
        if self._use_rich and self._progress is not None:
            # Print the completed track above the progress bar as a stable line.
            self._progress.console.print(f"  [green]✓[/green] {label}")
            self._progress.update(self._overall_task, completed=self._completed)
            self._progress.refresh()
        else:
            self._logger.info(
                "Completed %s (%s/%s)", label, self._completed, self._total
            )

    def advance_overall(self) -> None:
        """Advance the overall counter for skipped tracks (no per-track log)."""
        self._completed += 1
        if self._use_rich and self._progress is not None:
            self._progress.update(self._overall_task, completed=self._completed)
            self._progress.refresh()
        else:
            self._logger.info("Overall progress: %s/%s", self._completed, self._total)
