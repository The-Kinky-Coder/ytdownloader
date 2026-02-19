# Usage

This repository provides a CLI wrapper around yt-dlp.

After install, verify:

```bash
python3 --version
yt-dlp --version
ffmpeg -version
```

Logs (if your app writes any) should go to `/media/music/.logs`.

## CLI

```bash
ytdlp-wrapper "https://music.youtube.com/playlist?list=..."
```

If you prefer not to quote URLs with `&`, use `--url`:

```bash
ytdlp-wrapper --url https://music.youtube.com/playlist?list=...&si=...
```

### Audio format

By default the wrapper converts to `opus` for Navidrome compatibility. You can
override:

```bash
ytdlp-wrapper --audio-format m4a "https://music.youtube.com/playlist?list=..."
```

### Rate limiting

If you hit rate limits, increase request sleeps:

```bash
ytdlp-wrapper --sleep-requests 5 --sleep-interval 5 --max-sleep-interval 10 --concurrency 1 \
  "https://music.youtube.com/playlist?list=..."
```

### Metadata caching

The wrapper caches yt-dlp JSON metadata responses to reduce repeated `-J` calls.
Cached entries live for 30 days by default and are stored under
`~/.cache/ytdownloader/metadata`.

```bash
ytdlp-wrapper --metadata-cache-ttl-days 7 --metadata-cache-dir ~/.cache/ytdownloader/metadata \
  "https://music.youtube.com/playlist?list=..."
```

Disable caching or purge entries:

```bash
ytdlp-wrapper --disable-metadata-cache "https://music.youtube.com/playlist?list=..."
ytdlp-wrapper --purge-metadata-cache "https://music.youtube.com/playlist?list=..."
```

### Playlists (Navidrome)

When downloading a playlist, the tool writes an M3U file in the playlist
folder named after the playlist so Navidrome can auto-detect it on the next scan.

The M3U file includes `#EXTM3U` and `#EXTINF` entries and avoids duplicate
tracks on reruns.

If you only need to rebuild the M3U from existing files (no downloads), run:

```bash
ytdlp-wrapper --rewrite-m3u "/media/music/Brother Ali Mix"
```

To rewrite all playlist M3U files under your base music directory:

```bash
ytdlp-wrapper --rewrite-m3u-all
```

Progress bars are shown during downloads when running in a TTY.

### Cookies

```bash
ytdlp-wrapper --cookies ~/Downloads/cookies.txt "https://music.youtube.com/watch?v=..."
```

If you omit `--cookies`, the CLI will use the default cookies path when it exists and continue without cookies when it does not.

### Rate limit

```bash
ytdlp-wrapper --rate-limit 0 "https://music.youtube.com/playlist?list=..."
```

Use `--rate-limit 0` to disable throttling.

### SponsorBlock retries

When the SponsorBlock API is unreachable during a download, the wrapper:

1. Keeps the audio file (the download itself succeeded).
2. Retries SponsorBlock automatically once the full playlist finishes.
3. If the retry also fails, writes a `.pending.json` sidecar next to the audio file recording that SponsorBlock still needs to run.

On a later run, once the API is reachable again, you can replay all pending SponsorBlock jobs without re-downloading anything:

```bash
ytdlp-wrapper --retry-sponsorblock
```

This will:

- Bootstrap sidecars from `errors.log` for any pre-sidecar failures (one-time, idempotent).
- Scan `base_dir` recursively for `*.pending.json` files with a `sponsorblock` task.
- Re-run SponsorBlock segment removal on each matched audio file.
- Delete the sidecar once the task succeeds.

`--retry-sponsorblock` does not require a URL and does not download anything.
