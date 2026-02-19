# Pending-tasks sidecar system

## Overview

When a download completes successfully but a post-processing step fails (e.g.
the SponsorBlock API is unreachable), the wrapper writes a small JSON sidecar
file alongside the audio file instead of failing the whole download or silently
skipping the step. On a later run the user can replay only the failed
post-processing work without re-downloading anything.

The system lives in `src/ytdlp_wrapper/pending.py`.

## Sidecar file format

Sidecar files use the suffix `.pending.json` and sit in the same directory as
the audio file they describe:

```
/media/music/Brother Ali Mix/
  048-Brother Ali - Forest Whitiker.opus
  048-Brother Ali - Forest Whitiker.pending.json
```

```json
{
  "version": 1,
  "source_url": "https://music.youtube.com/watch?v=abc123",
  "output_stem": "048-Brother Ali - Forest Whitiker",
  "pending": ["sponsorblock"],
  "created": "2026-02-19T14:32:01"
}
```

| Field | Type | Description |
|---|---|---|
| `version` | int | Schema version (currently `1`) |
| `source_url` | string | Original YouTube / YT Music URL |
| `output_stem` | string | Filename stem (no extension, no directory) |
| `pending` | list[string] | Task tokens that still need to run |
| `created` | string | ISO-8601 timestamp when the sidecar was first created |

The `pending` list is modified in place as tasks succeed. When the list is
empty the file is deleted.

## Task tokens

| Token | Meaning | Retry command |
|---|---|---|
| `sponsorblock` | SponsorBlock segments have not been removed from the audio file | `--retry-sponsorblock` |

## Key functions (`pending.py`)

| Function | Purpose |
|---|---|
| `write_pending(audio_file, source_url, output_stem, tasks)` | Create or merge a sidecar for an audio file. Idempotent — merges tasks if the sidecar already exists. |
| `find_pending_sidecars(base_dir, task=None)` | Recursively scan `base_dir` for `*.pending.json` files. If `task` is given, filters to sidecars containing that token. |
| `PendingFile.remove_task(task)` | Remove a task token from the list. Deletes the sidecar if no tasks remain. |
| `PendingFile.save()` | Write the sidecar to disk. |
| `PendingFile.delete()` | Delete the sidecar unconditionally. |

## How `--retry-sponsorblock` works (`downloader.py`)

1. **Bootstrap** (`_bootstrap_pending_from_logs`): parses `errors.log` for
   `"SponsorBlock API unreachable after retries"` lines, cross-references
   `success.log` to recover source URLs, and creates sidecars for any failures
   that pre-date the sidecar system. This runs once per invocation and is
   idempotent.

2. **Scan**: `find_pending_sidecars(base_dir, task="sponsorblock")` walks the
   entire music library and returns all files still waiting for SponsorBlock.

3. **Retry**: for each `PendingFile`, re-runs the yt-dlp SponsorBlock
   post-processor against the audio file using the stored `source_url`.

4. **Clear**: on success, calls `pf.remove_task("sponsorblock")`, which
   deletes the sidecar if it is now empty.

## Extending the system for future post-processing tasks

The sidecar pattern is intentionally generic. To add a new deferrable
post-processing task:

### 1. Define a task token constant in `pending.py`

```python
PENDING_TASK_THUMBNAIL = "thumbnail"
```

### 2. Write the sidecar on failure

In whichever downloader function handles the new step, import `write_pending`
and call it when the step fails:

```python
from ytdlp_wrapper.pending import write_pending, PENDING_TASK_THUMBNAIL

# inside download_job() or similar, after the audio file is confirmed present:
write_pending(
    audio_file=job.output_path,
    source_url=job.source_url,
    output_stem=job.output_stem,
    tasks=[PENDING_TASK_THUMBNAIL],
    logger=logger,
)
```

### 3. Add a retry entry point in `downloader.py`

```python
def process_pending_thumbnails(config: Config, logger: logging.Logger) -> None:
    from ytdlp_wrapper.pending import find_pending_sidecars, PENDING_TASK_THUMBNAIL

    pending = find_pending_sidecars(
        Path(config.base_dir), task=PENDING_TASK_THUMBNAIL, logger=logger
    )
    logger.info("Found %d files with pending thumbnail work.", len(pending))
    for pf in pending:
        success = _reprocess_thumbnail(pf, config, logger)
        if success:
            pf.remove_task(PENDING_TASK_THUMBNAIL)
```

### 4. Wire up a CLI flag in `cli.py`

```python
parser.add_argument(
    "--retry-thumbnails",
    action="store_true",
    help="Reprocess embedded thumbnails for files that failed during download.",
)
```

```python
if args.retry_thumbnails:
    process_pending_thumbnails(config, logger)
    return
```

### Design notes

- A single audio file can carry **multiple task tokens** simultaneously.
  `write_pending` merges tasks idempotently, so multiple independent failures
  can accumulate without overwriting each other.
- Sidecar files are ignored by Navidrome and other media servers because they
  do not match any audio/image extension.
- The `version` field is reserved for future schema migrations. Currently all
  readers accept any version and fall back gracefully for unknown fields.
- Keep retry entry points **offline** (no URL argument required) — they operate
  on local files only.
