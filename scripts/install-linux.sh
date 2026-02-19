#!/usr/bin/env bash
set -euo pipefail

INSTALL_PIP_DEPS=false
VENV_PATH=""
MUSIC_DIR=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

for arg in "$@"; do
  case "$arg" in
    --with-pip-deps)
      INSTALL_PIP_DEPS=true
      ;;
    --venv-path=*)
      VENV_PATH="${arg#*=}"
      ;;
    --music-dir=*)
      MUSIC_DIR="${arg#*=}"
      ;;
    -h|--help)
      echo "Usage: $0 [--with-pip-deps] [--music-dir=/path/to/music]"
      echo "  --with-pip-deps        Install optional pip packages (rich, mutagen) into a venv"
      echo "  --venv-path=/path      Override venv location (default: /home/<user>/.ytdlp-wrapper-venv)"
      echo "  --music-dir=/path      Music download directory (will be prompted if not provided)"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ "$EUID" -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This installer currently supports Debian/Ubuntu (apt-get required)." >&2
  exit 1
fi

# Prompt for music directory if not provided via flag
if [[ -z "$MUSIC_DIR" ]]; then
  echo ""
  read -rp "Where should music be downloaded? [/media/music]: " MUSIC_DIR
  MUSIC_DIR="${MUSIC_DIR:-/media/music}"
fi
# Strip trailing slash
MUSIC_DIR="${MUSIC_DIR%/}"
echo "Music directory: $MUSIC_DIR"

export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y --no-install-recommends python3 python3-venv ffmpeg

if ! command -v yt-dlp >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends yt-dlp
fi

LOG_DIR="${MUSIC_DIR}/.logs"
mkdir -p "$LOG_DIR"
chmod 775 "$LOG_DIR"
if [[ -n "${SUDO_USER:-}" ]]; then
  chown -R "$SUDO_USER":"$SUDO_USER" "$LOG_DIR"
fi

# Write user config file so the CLI knows where music lives
if [[ -n "${SUDO_USER:-}" ]]; then
  CONFIG_DIR="/home/${SUDO_USER}/.config/ytdlp-wrapper"
else
  CONFIG_DIR="${HOME}/.config/ytdlp-wrapper"
fi
mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_DIR/config.ini" <<EOF
[ytdlp-wrapper]
base_dir = ${MUSIC_DIR}

# SponsorBlock: comma-separated list of categories to remove from downloads.
# Remove the leading '#' on the line below to enable.
# sponsorblock_categories = sponsor,selfpromo,interaction
EOF
if [[ -n "${SUDO_USER:-}" ]]; then
  chown -R "$SUDO_USER":"$SUDO_USER" "$CONFIG_DIR"
fi
echo "Config written to: $CONFIG_DIR/config.ini"
echo "Cookies: place cookies.txt in $CONFIG_DIR/ and it will be used automatically."

if $INSTALL_PIP_DEPS; then
  if [[ -z "$VENV_PATH" ]]; then
    if [[ -n "${SUDO_USER:-}" ]]; then
      VENV_PATH="/home/${SUDO_USER}/.ytdlp-wrapper-venv"
    else
      VENV_PATH="$PWD/.venv"
    fi
  fi

  echo "Creating venv at: $VENV_PATH"
  python3 -m venv "$VENV_PATH"
  "$VENV_PATH/bin/python" -m pip install --upgrade pip
  "$VENV_PATH/bin/python" -m pip install rich mutagen

  echo "Installing CLI into venv from: $REPO_ROOT"
  "$VENV_PATH/bin/python" -m pip install -e "$REPO_ROOT"

  if [[ -n "${SUDO_USER:-}" ]]; then
    chown -R "$SUDO_USER":"$SUDO_USER" "$VENV_PATH"
  fi

  echo "Optional deps + CLI installed in venv: $VENV_PATH"
  echo "Installing launcher at /usr/local/bin/ytdlp-wrapper"

  cat > /usr/local/bin/ytdlp-wrapper <<'EOF'
#!/usr/bin/env bash
exec "__VENV_BIN__" "$@"
EOF
  sed -i "s|__VENV_BIN__|$VENV_PATH/bin/ytdlp-wrapper|g" /usr/local/bin/ytdlp-wrapper
  chmod 755 /usr/local/bin/ytdlp-wrapper
fi

if ! command -v ytdlp-wrapper >/dev/null 2>&1; then
  echo "Install the CLI by running from repo: pip install -e ." >&2
fi

echo "Installation complete."
