# ytdownloader

YouTube Music downloader CLI wrapper around yt-dlp with progress bars and structured logs.

## Quick Start

1. Install dependencies:

```bash
sudo ./scripts/install-linux.sh
```

2. Optional pip extras (installed into a venv):

```bash
sudo ./scripts/install-linux.sh --with-pip-deps
```

This will create a venv, install the CLI into it, and add a launcher at
`/usr/local/bin/ytdlp-wrapper` so you can run the command without activating
the venv.

3. If you skip pip deps, install the CLI manually (from repo):

```bash
pip install -e .
```

4. Run the CLI:

```bash
ytdlp-wrapper "https://music.youtube.com/playlist?list=..."
```

If you prefer not to quote URLs with `&`, use `--url`:

```bash
ytdlp-wrapper --url https://music.youtube.com/playlist?list=...&si=...
```

Logging lives under `/media/music/.logs` with per-run logs plus success/skipped/errors/retries files. Rate limiting can be disabled with `--rate-limit 0`. Cookies are optional: pass `--cookies` or rely on the default path if present.

Audio is converted to `opus` by default for Navidrome compatibility. Override with `--audio-format m4a` if needed.

When downloading playlists, the tool writes an M3U file named after the playlist
inside the playlist folder so Navidrome can auto-detect it on the next library scan.
The M3U includes `#EXTM3U` and `#EXTINF` entries and avoids duplicates on reruns.

You can rebuild playlist files from existing downloads (no network):

```bash
ytdlp-wrapper --rewrite-m3u "/media/music/Brother Ali Mix"
ytdlp-wrapper --rewrite-m3u-all
```

If you hit YouTube rate limits, try lowering concurrency and increasing request
sleep values (see `docs/usage.md`).

See full docs in `docs/`.

## Troubleshooting

**Playlist imports missing tracks in Navidrome**

Regenerate the M3U using existing files:

```bash
ytdlp-wrapper --rewrite-m3u "/media/music/Your Playlist"
# or rebuild all playlists
ytdlp-wrapper --rewrite-m3u-all
```

**YouTube rate limiting**

Lower concurrency and increase sleep values:

```bash
ytdlp-wrapper --concurrency 1 --sleep-requests 5 --sleep-interval 5 --max-sleep-interval 10
```

**Metadata cache issues**

Clear cache and retry:

```bash
ytdlp-wrapper --purge-metadata-cache
```
