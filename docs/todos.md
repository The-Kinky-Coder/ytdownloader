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

### Sidecar-based feature ideas

The `.pending.json` sidecar system (see [docs/pending-tasks.md](pending-tasks.md))
makes it straightforward to add deferred post-processing for other failure modes.
Possible future tasks using the same pattern:

- **Thumbnail reprocessing** (`thumbnail` token): if embedding the album art
  fails during download, write a sidecar and add `--retry-thumbnails` to replay
  it later via a dedicated ffmpeg pass.
- **Metadata refresh** (`metadata` token): if a track's tags look stale or
  incomplete, flag it for a metadata-only re-fetch and re-embed without
  re-downloading the audio.
- **Format conversion** (`convert` token): if a file was downloaded in a
  format that later becomes unsupported by the target media server, flag it
  for re-encoding without hitting the network.
- **General `--process-pending`**: a single catch-all flag that dispatches
  all pending task tokens rather than requiring per-task flags.
