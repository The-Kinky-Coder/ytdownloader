# Configuration

## Config file

User settings are stored in `~/.config/ytdlp-wrapper/config.ini`. This is created automatically by the installer. You can also create or edit it manually:

```ini
[ytdlp-wrapper]
base_dir = /media/music
```

The config file is optional. If it doesn't exist, hardcoded defaults are used. CLI flags always take priority over the config file.

Supported keys:

| Key | Description | Default |
|---|---|---|
| `base_dir` | Music download directory | `/media/music` |
| `log_dir` | Log directory | `<base_dir>/.logs` |
| `download_archive` | yt-dlp archive file | `<log_dir>/download_archive.txt` |

## CLI flags

All settings can also be overridden per-run via CLI flags. Defaults shown below are used when neither the config file nor a CLI flag specifies a value.

- Base directory: `/media/music`
- Log directory: `<base_dir>/.logs`
- Cookies file: `~/.config/yt-dlp/cookies.txt`
- Concurrency: 5
- Sleep interval: 1s (max 3s)
- Sleep between metadata requests: 2s
- Rate limit: 2M
- Download archive: `<log_dir>/download_archive.txt`
- Audio format: `opus`
- Metadata cache directory: `~/.cache/ytdownloader/metadata`
- Metadata cache TTL: 30 days

Use `--rate-limit 0` to disable throttling.
Use `--audio-format opus` (default) or `--audio-format m4a` for wider compatibility.
Use `--disable-metadata-cache` to skip cached metadata and `--purge-metadata-cache` to delete cached entries.

Cookies are optional. If `--cookies` is not provided, the default cookies path is used when it exists.
