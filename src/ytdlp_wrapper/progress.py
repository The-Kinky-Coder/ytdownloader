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
    def __init__(self, total: int, logger: logging.Logger) -> None:
        self._logger = logger
        self._total = total
        self._completed = 0
        self._tasks: dict[str, DownloadTask] = {}
        self._use_rich = _RICH_AVAILABLE
        self._progress: Any = None
        self._overall_task: Any = None

        if self._use_rich:
            from rich.progress import (
                BarColumn,
                DownloadColumn,
                Progress,
                TextColumn,
                TimeRemainingColumn,
                TransferSpeedColumn,
            )

            self._progress = Progress(
                TextColumn("[bold]{task.fields[label]}"),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                TextColumn("{task.percentage:>5.1f}%"),
            )
            self._overall_task = self._progress.add_task(
                "overall",
                total=total,
                label="Overall",
            )

    def __enter__(self) -> "ProgressReporter":
        if self._use_rich and self._progress is not None:
            self._progress.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._use_rich and self._progress is not None:
            self._progress.stop()

    def add_task(self, key: str, label: str, total: int | None = None) -> None:
        if self._use_rich and self._progress is not None:
            task_id = self._progress.add_task(label, total=total or 100, label=label)
            self._tasks[key] = DownloadTask(task_id=task_id, label=label)
        else:
            self._logger.info("Starting: %s", label)

    def update(
        self, key: str, completed: float | None = None, total: float | None = None
    ) -> None:
        if self._use_rich and self._progress is not None:
            task = self._tasks.get(key)
            if not task:
                return
            if total is not None:
                self._progress.update(task.task_id, total=total)
            if completed is not None:
                self._progress.update(task.task_id, completed=completed)
        else:
            if completed is not None and total:
                percent = (completed / total) * 100
                self._logger.info("Progress %s: %.1f%%", key, percent)

    def complete(self, key: str) -> None:
        self._completed += 1
        if self._use_rich and self._progress is not None:
            if key in self._tasks:
                self._progress.update(self._tasks[key].task_id, completed=100)
            if self._overall_task is not None:
                self._progress.update(self._overall_task, completed=self._completed)
        else:
            self._logger.info("Completed %s (%s/%s)", key, self._completed, self._total)

    def advance_overall(self) -> None:
        self._completed += 1
        if self._use_rich and self._progress is not None:
            if self._overall_task is not None:
                self._progress.update(self._overall_task, completed=self._completed)
        else:
            self._logger.info("Overall progress: %s/%s", self._completed, self._total)
