# Installation

## Linux (Debian/Ubuntu)

```bash
sudo ./scripts/install-linux.sh
```

Optional pip packages:

```bash
sudo ./scripts/install-linux.sh --with-pip-deps
```

This creates a virtual environment (venv) for optional Python packages, installs
the CLI into it, and creates a launcher at `/usr/local/bin/ytdlp-wrapper` so you
can run the CLI without activating the venv. By default it creates:

```
/home/<user>/.ytdlp-wrapper-venv
```

Override with:

```bash
sudo ./scripts/install-linux.sh --with-pip-deps --venv-path=/path/to/venv
```

If you skip `--with-pip-deps`, install the CLI from the repo (editable):

```bash
pip install -e .
```

The script installs:
- python3
- python3-venv
- yt-dlp
- ffmpeg
- Optional: rich, mutagen

`rich` enables progress bars in the CLI.
