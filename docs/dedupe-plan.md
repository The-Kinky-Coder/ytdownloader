# Dedupe Subcommand Plan (Draft)

## Goals
- Identify duplicate audio files across the library.
- Support safe dedupe modes (report-only, move to quarantine, delete with confirmation).
- Preserve metadata and avoid false positives when possible.

## Proposed CLI Surface
- `ytdlp-wrapper dedupe --base-dir /media/music`
- Flags:
  - `--mode report|quarantine|delete`
  - `--hash algo` (default: sha256)
  - `--min-size-kb` (skip tiny files)
  - `--extensions mp3,opus,m4a,flac`
  - `--quarantine-dir` (default: `/media/music/.quarantine`)
  - `--dry-run` (default for delete/quarantine)
  - `--json` output for reporting

## Approach
1. Scan base directory for audio files matching extensions.
2. Group by size to reduce hash work.
3. For groups with matching sizes, compute hashes (chunked reads).
4. For exact hash matches, select a canonical file (older mtime or shortest path).
5. Produce report and execute mode actions with logs.

## Safety Considerations
- Default to report-only mode.
- Require explicit `--mode delete` and confirmation (unless `--yes`).
- Quarantine mode should preserve original paths in a manifest.
- Skip files that are currently being downloaded (optional lock file check).

## Output & Logs
- Write report to log dir: `dedupe_report.json` + `dedupe_report.txt`.
- Record actions in `dedupe_actions.log`.

## Tests
- Unit tests for grouping logic, hashing, and canonical selection.
- Integration test with temp directory and sample files.

## Dependencies
- None required; use standard library hashlib and pathlib.
