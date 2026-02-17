# Logging

The installer ensures a log directory exists at `/media/music/.logs`.

Logs produced by the CLI:

- `ytdlp_wrapper.log`: run logs with errors and retry attempts
- `metadata_mismatch.log`: files skipped due to metadata differences
- `download_archive.txt`: yt-dlp download archive for resuming
- `success.log`: successful downloads for the run
- `skipped.log`: skipped entries (metadata mismatch, existing files, etc.)
- `errors.log`: failed downloads
- `retries.log`: retry attempts per URL

Permissions are set to `775` for group write access.
