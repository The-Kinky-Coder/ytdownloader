# Configuration

Defaults are managed in `src/ytdlp_wrapper/config.py` and overridden via CLI flags.

Key defaults:

- Base directory: `/media/music`
- Log directory: `/media/music/.logs`
- Cookies file: `~/.config/yt-dlp/cookies.txt`
- Concurrency: 5
- Sleep interval: 1s (max 3s)
- Sleep between metadata requests: 2s
- Rate limit: 2M
- Download archive: `/media/music/.logs/download_archive.txt`
- Audio format: `opus`
- Metadata cache directory: `~/.cache/ytdownloader/metadata`
- Metadata cache TTL: 30 days

Use `--rate-limit 0` to disable throttling.
Use `--audio-format opus` (default) or `--audio-format m4a` for wider compatibility.
Use `--disable-metadata-cache` to skip cached metadata and `--purge-metadata-cache` to delete cached entries.

Cookies are optional. If `--cookies` is not provided, the default cookies path is used when it exists.
