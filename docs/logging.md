# Logging

The installer ensures a log directory exists at `/media/music/.logs`.

Logs produced by the CLI:

- `ytdlp_wrapper.log`: run logs with errors and retry attempts
- `metadata_mismatch.log`: files skipped due to metadata differences
- `download_archive.txt`: yt-dlp download archive for resuming
- `success.log`: successful downloads for the run
- `skipped.log`: skipped entries (metadata mismatch, existing files, etc.)
- `errors.log`: failed downloads and SponsorBlock status markers (see below)
- `retries.log`: retry attempts per URL

Permissions are set to `775` for group write access.

## Sidecar files (`.pending.json`)

When a download succeeds but a post-processing step fails (e.g. the SponsorBlock
API was unreachable), the wrapper writes a small JSON sidecar file **next to the
audio file** (not in the log directory):

```
/media/music/Brother Ali Mix/
  048-Brother Ali - Forest Whitiker.opus
  048-Brother Ali - Forest Whitiker.pending.json   ← sidecar
```

Sidecar format:

```json
{
  "version": 1,
  "source_url": "https://music.youtube.com/watch?v=abc123",
  "output_stem": "048-Brother Ali - Forest Whitiker",
  "pending": ["sponsorblock"],
  "created": "2026-02-19T14:32:01"
}
```

The `pending` list contains task tokens for work that still needs to be done.
Once all tasks succeed, the sidecar is deleted automatically.

Currently defined task tokens:

| Token | Meaning |
|---|---|
| `sponsorblock` | SponsorBlock segment removal has not yet been applied to this file |

See [docs/pending-tasks.md](pending-tasks.md) for architecture details and how
to extend the system for future post-processing tasks.

## SponsorBlock markers in `errors.log`

`--retry-sponsorblock` writes two types of marker lines to `errors.log`:

**Transient failure** — API was unreachable; sidecar kept for next attempt:

```
2026-02-19T16:01:05 048-Artist-Track | SponsorBlock retry failed — sidecar kept for next attempt
```

**Resolved (no segments)** — SponsorBlock confirmed this video has no segments
in its database; the file is clean and the sidecar has been removed:

```
2026-02-19T16:15:22 048-Artist-Track | SponsorBlock resolved — no segments in database
```

The bootstrap (`_bootstrap_pending_from_logs`) reads both types.  Stems with a
resolved marker are permanently excluded from sidecar creation on future runs —
they will never appear in `--retry-sponsorblock` again.  The `sponsorblock_done.log`
file (used in earlier versions) is no longer written or read.
