TASK: Create a YouTube Music Downloader CLI Tool
Create a command-line tool for downloading audio from YouTube Music using yt-dlp. The tool should support both playlist and single video downloads with proper organization and metadata handling.
CORE REQUIREMENTS:

Download Engine:

Use yt-dlp as the download backend
Require ffmpeg
Optional Python libs: mutagen and rich (if using a Python CLI wrapper, that's acceptable)
Use Firefox cookies from ~/.config/yt-dlp/cookies.txt for authentication
Also accept a CLI cookies arg: --cookies /path/to/cookies.txt; copy to ~/.config/yt-dlp/cookies.txt (create dir if missing)
Download best quality audio available (no transcoding needed - preserve original format)


Command Interface:

Accept YouTube Music URL as command-line argument
Example usage: <command> https://music.youtube.com/playlist?list=PLDuhRYqIcAl2vYLY04gJrLkB5N6jFnVKm
Support both playlist URLs and single video URLs


Download Management:

Parallel downloads: 5 concurrent downloads by default
Implement appropriate rate limiting to avoid YouTube throttling
Defaults: --sleep-interval 1, --max-sleep-interval 3; optional --rate-limit 2M (all configurable)
Retry failed downloads 2-3 times before giving up
If download continues to fail after retries, exit with clear error message explaining the reason
Resume support: use yt-dlp native resume + --download-archive (if appropriate); note this is sufficient


File Organization:

Base directory: /media/music
For playlists: Create folder named after playlist, containing all tracks
For single videos: Organize as Artist/Album/songname.ext (determine from metadata)
Sanitize all folder/file names to prevent filesystem issues (remove/replace special characters)


File Naming Convention:

Playlist tracks: <track_number>-<artist>-<trackname>.ext

Track number should have preceding zeros (01, 02, etc.)


Single videos: <artist>-<trackname>.ext (no track number)
Example: 01-Pink Floyd-Comfortably Numb.opus


Metadata Handling:

Preserve and embed all available metadata (artist, album, title, cover art, track number, etc.)
Format metadata to be compatible with Navidrome music server
Store metadata in the audio file itself when the format supports it
For single videos: Extract artist/album from YouTube Music metadata first; if unavailable, parse from video title/description


Duplicate Handling:

Skip files that already exist
Notify user when skipping: "Skipping: <filename> (already exists)"
If filename matches but metadata differs, skip it (don't re-download) and log to metadata_mismatch.log


User Interface:

Modern terminal UI showing download progress
Display: current file downloading, progress bars, download speed, ETA
Show parallel download status (all 5 concurrent downloads)
One progress bar per active download (max 5) plus an overall playlist progress bar
Clear feedback for skipped files, errors, and completion


Logging:

Log file location: /media/music/.logs/
Log successful downloads, skipped files, errors, and retry attempts
Include timestamps and relevant details for debugging


Installation & Dependencies:


Create an installation script for easy setup/reinstallation
During development/testing: auto-install missing dependencies as needed
Check for yt-dlp and other required tools; provide clear error if missing critical dependencies
Create /media/music/.logs and set permissions

TECHNICAL NOTES:

The music player supports many audio formats, so preserve original format (opus, m4a, etc.)
Playlist folder names should be sanitized if too long or contain problematic characters
Consider edge cases: empty playlists, private videos, geo-restricted content

DELIVERABLES:

Main download script/tool
Installation script for easy deployment
Brief usage documentation

DEVELOPMENT ENVIRONMENT:

Dev Machine: Windows with WSL (Windows Subsystem for Linux) available
Target/Production Machine: Linux system (separate from dev machine)
You have access to:

Local Windows/WSL environment for development
Can be provided a terminal/SSH session to the target Linux machine for testing/deployment


Important:

Check your current development environment at the start
The tool must run on the Linux target machine (where /media/music is located)
You can request access to the target machine terminal for testing/deployment when needed
Development and testing should account for this split environment
The installation script should be designed for the Linux target, not Windows/WSL
