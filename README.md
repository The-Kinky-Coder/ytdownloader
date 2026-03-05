# ytdownloader

YouTube Music downloader CLI that wraps yt-dlp with Navidrome-compatible tagging, SponsorBlock trimming, M3U playlist generation, and a clean progress UI.

> **Note:** This tool was built primarily for my own use. The defaults (download location, audio format, Navidrome tagging conventions, etc.) reflect my personal setup. It works well for me but your mileage may vary.

## Requirements

- Debian/Ubuntu Linux (installer uses `apt-get`)
- Python 3.10+
- `yt-dlp` and `ffmpeg` (installed by the installer)
- Optional: `rich`, `mutagen` pip packages (nicer UI and tag rewriting)

## Installation

```bash
sudo ./scripts/install-linux.sh
```

The installer will:
- Install `yt-dlp`, `ffmpeg`, and `python3` via apt
- Prompt for your music directory (default: `/media/music`)
- Create `~/.config/ytdlp-wrapper/config.ini` with your settings

To also install the pip extras and register `ytdlp-wrapper` as a system command:

```bash
sudo ./scripts/install-linux.sh --with-pip-deps
```

This creates a venv, installs `rich` and `mutagen`, and adds a launcher at `/usr/local/bin/ytdlp-wrapper`.

If you skip `--with-pip-deps`, install the CLI manually from the repo root:

```bash
pip install -e .
```

## Configuration

All config lives in one place:

```
~/.config/ytdlp-wrapper/
├── config.ini      # music directory, SponsorBlock categories
└── cookies.txt     # optional yt-dlp cookies (placed here, used automatically)
```

Minimal `config.ini`:

```ini
[ytdlp-wrapper]
base_dir = /media/music

# SponsorBlock trimming is **enabled by default** using the most
# common categories.  You only need to uncomment/modify the line below if you
# want to change or disable the defaults:
# sponsorblock_categories = sponsor,selfpromo,interaction
```

Logs are written to `<base_dir>/.logs/`.

See `docs/configuration.md` for all supported keys and CLI flag reference.

## Usage

Download a playlist or single track:

```bash
ytdlp-wrapper "https://music.youtube.com/playlist?list=..."
# or use --url to avoid shell quoting issues with & characters:
ytdlp-wrapper --url https://music.youtube.com/playlist?list=...
```

By default playlists are treated as Various Artists compilations so Navidrome groups
all tracks under a single album.  If you happen to be downloading a normal
single-artist album from a playlist, pass `--no-compilation` to disable that
tagging behaviour for the current run (the default is preserved for later).

If no URL is provided you will be prompted for one interactively.

### Audio normalization

By default the CLI normalizes the loudness of every file it downloads using
ffmpeg's EBU‑R128 two‑pass filter.  Normalization occurs *after* downloads
complete so the concurrent download threads aren't slowed by the CPU‑heavy
processing.  Files already tagged with the easy mutagen key
`normalized=1` are skipped on subsequent runs.

You can disable or adjust normalization with flags or config:

```bash
# disable entirely
ytdlp-wrapper --no-normalize "<url>"

# tune behaviour
ytdlp-wrapper --normalize-workers 4 --normalize-lufs -16 "<url>"

ytdlp-wrapper --normalize-background "<url>"  # return immediately, run in background
```

This feature is automatically enabled; to opt out set
`normalize = false` in `~/.config/ytdlp-wrapper/config.ini`.

### M3U playlists

A `.m3u` file is written alongside each downloaded playlist so Navidrome can detect it automatically. To rebuild M3U files from existing files on disk without re-downloading:

```bash
ytdlp-wrapper --rewrite-m3u "/media/music/Brother Ali Mix"
ytdlp-wrapper --rewrite-m3u-all
```

### Re-downloading playlists

To re-download all playlists with SponsorBlock applied, using stored playlist URLs:

```bash
# First time: stamp any playlist folders that are missing their stored URL
ytdlp-wrapper --stamp-missing-urls

# Then re-download all playlists atomically
ytdlp-wrapper --reprocess-playlists
```

Downloads go to a temp directory first and are swapped in only on success, so originals are preserved if anything fails.

### SponsorBlock retries

If the SponsorBlock API is down during a download run, tracks are still downloaded normally. The audio is fine, just without sponsor segment removal. The tool writes a small `.pending.json` sidecar file next to each affected track to record what still needs doing.

Once the API has recovered, apply SponsorBlock to all pending tracks in one go:

```bash
ytdlp-wrapper --retry-sponsorblock
```

### Thumbnail recovery

Downloaded tracks normally embed the YouTube/thumb art into the audio file; however not every container supports embedded images and embedding can fail silently. When that happens the wrapper now copies whatever thumbnail file yt-dlp saved (``*.webp``/``.jpg``) to `folder.jpg` beside the audio so Navidrome will always see a cover image.

If an album still ends up without any art, a ``thumbnail`` task is recorded in the
sidecar file.  You can repair those later without re-downloading:

```bash
ytdlp-wrapper --retry-thumbnails
```

Older playlist folders without any cover art can be fixed in bulk using the
new ``--generate-thumbnails`` command.  It scans the given directory (or, if
none is supplied, every subdirectory under ``base_dir``) and attempts to
recreate the same thumbnail that would have been added during a download
(either by fetching it again from yt-dlp/YouTube or by extracting embedded
art from the first track).  No audio is downloaded, only image files are
created.  A progress bar will appear when running in a TTY so you can watch
the tool work through each playlist folder.

The command will copy any remaining thumbnail files into place and remove the
pending token when successful.  See `docs/pending-tasks.md` for more details.

This scans your entire music directory for sidecar files, retries SponsorBlock post-processing for each, and removes the sidecar on success. Tracks that still fail are left with their sidecar in place so you can try again later. The command is safe to run multiple times.

> If you had SponsorBlock failures before this sidecar system was introduced, `--retry-sponsorblock` will also scan `errors.log` and `success.log` to bootstrap sidecars for those historic failures automatically.

See `docs/pending-tasks.md` for more on the sidecar architecture.

### Tag fixes

If Navidrome is splitting a playlist into per-artist ghost albums, fix the compilation tags:

```bash
ytdlp-wrapper --retag "/media/music/Your Playlist"
ytdlp-wrapper --retag-all
```

## Troubleshooting

**SponsorBlock API errors during download**

Tracks still download correctly. Sidecar files (`.pending.json`) are written next to each affected track. Once the API recovers:

```bash
ytdlp-wrapper --retry-sponsorblock
```

**Playlist missing tracks in Navidrome**

Regenerate the M3U from existing files:

```bash
ytdlp-wrapper --rewrite-m3u "/media/music/Your Playlist"
ytdlp-wrapper --rewrite-m3u-all
```

**YouTube rate limiting**

Lower concurrency and increase sleep values:

```bash
ytdlp-wrapper --concurrency 1 --sleep-requests 5 --sleep-interval 5 --max-sleep-interval 10
```

**Tracks re-downloading that you already have**

The download archive at `<base_dir>/.logs/download_archive.txt` tracks what has been downloaded. On each run, entries whose files are missing from disk are automatically scrubbed so yt-dlp re-downloads them naturally.

**Metadata cache issues**

```bash
ytdlp-wrapper --purge-metadata-cache
```
