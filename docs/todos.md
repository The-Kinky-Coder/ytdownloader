# TODOs / Assumptions

## Assumptions

- Playlist entries fetched via `yt-dlp -J` require per-entry metadata fetch for consistent tags.
- FFmpeg is installed and accessible on PATH.
- yt-dlp will handle resume and archive behavior appropriately when `--download-archive` is present.
- Rich is available for progress bars when installed; otherwise downloads run without them.

## TODOs

- Draft and implement a dedupe subcommand (see docs/dedupe-plan.md).
- Improve metadata diffing logic across container formats.
- Add unit tests for sanitize, naming, and metadata parsing.
