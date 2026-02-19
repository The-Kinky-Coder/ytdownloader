# Configuration

All configuration lives in one directory: `~/.config/ytdlp-wrapper/`

```
~/.config/ytdlp-wrapper/
├── config.ini      # main settings (paths, SponsorBlock categories)
└── cookies.txt     # optional yt-dlp cookies file
```

## config.ini

Created automatically by the installer. You can also create or edit it manually.

```ini
[ytdlp-wrapper]
base_dir = /media/music

# SponsorBlock: comma-separated list of categories to remove from downloads.
# Remove the leading '#' on the line below to enable.
# sponsorblock_categories = sponsor,selfpromo,interaction
```

The config file is optional. If it doesn't exist, hardcoded defaults are used.
CLI flags always take priority over the config file.

Supported keys:

| Key | Description | Default |
|---|---|---|
| `base_dir` | Music download directory | `/media/music` |
| `log_dir` | Log directory | `<base_dir>/.logs` |
| `download_archive` | yt-dlp archive file | `<log_dir>/download_archive.txt` |
| `sponsorblock_categories` | Comma-separated SponsorBlock categories to remove | _(disabled)_ |

Common SponsorBlock categories: `sponsor`, `selfpromo`, `interaction`, `intro`, `outro`, `preview`, `filler`, `music_offtopic`.

> **Note:** `sponsorblock_categories` must be configured (non-empty) for `--retry-sponsorblock`
> to apply segment removal. If SponsorBlock is disabled in config, the retry command will
> find and list pending sidecars but will not remove any segments.

## cookies.txt

Place your yt-dlp cookies file at `~/.config/ytdlp-wrapper/cookies.txt`.
On startup, if this file exists and `~/.config/yt-dlp/cookies.txt` does not,
it is automatically copied to the yt-dlp standard location.

You can also pass `--cookies /path/to/cookies.txt` on the CLI to copy from any location.

## CLI flags

All settings can also be overridden per-run via CLI flags.
Defaults shown below apply when neither the config file nor a CLI flag specifies a value.

- Base directory: `/media/music`
- Log directory: `<base_dir>/.logs`
- Download archive: `<log_dir>/download_archive.txt`
- Cookies file: `~/.config/ytdlp-wrapper/cookies.txt` → `~/.config/yt-dlp/cookies.txt`
- Concurrency: 5
- Sleep interval: 1s (max 3s)
- Sleep between metadata requests: 2s
- Rate limit: 2M
- Audio format: `opus`
- Metadata cache directory: `~/.cache/ytdownloader/metadata`
- Metadata cache TTL: 30 days

Use `--rate-limit 0` to disable throttling.
Use `--audio-format opus` (default) or `--audio-format m4a` for wider compatibility.
Use `--disable-metadata-cache` to skip cached metadata and `--purge-metadata-cache` to delete cached entries.
