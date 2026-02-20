# Revert Feature: Push Old Content Back to Google Drive

## Context

gdrive-git-sync currently only syncs Drive->git (one-way, readonly). Users want to undo Drive changes by pushing old content back. Since git only stores lossy exports of Google Workspace files (Docs->docx->markdown), we need a dual-source strategy: **git history for binary files, Drive revisions API for Workspace files**.

This adds a local CLI tool (not a Cloud Function) that reuses existing modules. The write capability is opt-in via explicit scope injection (not a config flag).

## Changes

### 1. `functions/drive_client.py` — Add revision + write methods, scope injection

**Scope**: Change from hardcoded `drive.readonly` to constructor parameter with readonly default. No config flag -- the caller explicitly opts in.

```python
class DriveClient:
    SCOPE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
    SCOPE_READWRITE = "https://www.googleapis.com/auth/drive"

    def __init__(self, service=None, *, scope: str | None = None):
        # scope defaults to SCOPE_READONLY if not provided
```

Cloud Functions call `DriveClient()` (unchanged, gets readonly). CLI calls `DriveClient(scope=DriveClient.SCOPE_READWRITE)`.

**New methods:**
- `list_revisions(file_id)` — paginated listing via `revisions().list()`
- `download_revision(file_id, revision_id, mime_type)` — **two paths depending on file type:**
  - **Binary files**: `revisions().get_media()` (direct download)
  - **Workspace files**: `revisions().get(fields='exportLinks')` to get per-format export URLs, then `AuthorizedSession.get(url)` to download. Error if `exportLinks` is empty.
  - (`revisions().get_media()` returns `fileNotDownloadable` for Workspace files -- verified via API docs. `files.download()` returns an async Operation and doesn't support Slides. `exportLinks` is the only viable simple path.)
- `update_file_content(file_id, content, mime_type)` — `files().update()` with `MediaIoBaseUpload(io.BytesIO(content))` (not `MediaInMemoryUpload` which is deprecated)
- `get_file_metadata(file_id)` — fetch current metadata for validation
- `check_comments(file_id)` — `comments().list(fileId)` to count existing comments
- `check_suggestions(file_id)` — `build("docs", "v1").documents().get(documentId)`, walk body for `suggestedInsertionIds`/`suggestedDeletionIds` in TextRun elements (no new pip dep -- same `google-api-python-client` package, `drive` scope covers Docs API access)

### 2. `functions/revert_engine.py` — New file (~250 lines)

Core orchestration:
- `resolve_path_to_file(path, state)` — look up file_id from Firestore by path
- `is_workspace_file(mime_type)` — check against `GOOGLE_NATIVE_EXPORTS` keys
- `list_versions(file_id, file_data, drive, git_repo_path)` — merge Drive revisions + git log entries, sorted by time
- `check_destructive_impact(file_id, mime_type, drive)` — for Workspace files, check comments (Drive API) + suggestions (Docs API). Returns warning message if either exists.
- `revert_file_to_revision(file_id, revision_id, drive, dry_run)` — download old revision via `exportLinks` (Workspace) or `get_media` (binary), re-upload
- `revert_file_to_commit(file_id, file_data, commit_sha, drive, git_repo_path, dry_run)` — extract from git via `subprocess.run(["git", "show", ...])` **without `text=True`** (binary content), upload. Blocks Workspace files with clear error.
- `revert_folder_to_time(folder_path, target_time, drive, state, git_repo_path, dry_run)` — for each file, find closest version before target time, revert using appropriate source

### 3. `functions/cli.py` — New file (~170 lines)

argparse CLI with two subcommands:

```
python functions/cli.py revisions <path> [--drive-only]
python functions/cli.py revert <path> --to-revision <id> [--dry-run] [--force]
python functions/cli.py revert <path> --to-commit <sha> [--dry-run] [--force]
python functions/cli.py revert <folder/> --to-time <datetime> [--dry-run] [--force]
```

- `revisions` — lists available versions (table: source, ID, time, author, summary)
- `revert` — requires exactly one of `--to-revision`, `--to-commit`, `--to-time`
- For Workspace files: runs `check_destructive_impact()`. If comments or suggestions exist, prints count and requires `--force` to proceed
- Confirmation prompt before actual revert (skip with `--force`)

### 4. `functions/config.py` — No changes needed

The scope is injected at the `DriveClient` constructor, not via config. Cloud Functions are never exposed to write scope.

### 5. `pyproject.toml` — Add new modules to isort known-first-party

### 6. `scripts/setup.sh` — Informational note

No setup step needed since there's no config flag. The CLI uses ADC with `drive` scope. Setup can add a note: "To use `revert`, ensure the Drive folder is shared with Editor permissions for write-back."

## Files NOT changed
- `functions/config.py` — no write flag needed (scope is constructor-injected)
- `functions/main.py` — Cloud Functions unaffected
- `functions/git_ops.py` — CLI uses `subprocess` for `git show` directly (not GitRepo class, to avoid full clone overhead when only fetching one blob). Blobless clone auto-fetches blobs on demand (verified).
- `functions/state_manager.py` — `get_all_files()` and `get_files_in_folder()` already exist
- `functions/sync_engine.py` — forward sync untouched
- `infra/*.tf` — CLI runs locally, no infra changes

## Auth model
- Cloud Functions: `DriveClient()` -> `drive.readonly` (unchanged, default)
- CLI: `DriveClient(scope=DriveClient.SCOPE_READWRITE)` -> `drive` scope
- Same credential source (ADC), different scope at call site
- Drive folder needs Editor sharing for the identity running the CLI (user account or service account)
- Scope is never ambient/env-driven -- prevents accidental write escalation in Cloud Functions

## Key design decisions
- **No `DRIVE_WRITE_ENABLED` config flag** — scope is constructor-injected, not environment-driven. Prevents accidental write scope in Cloud Functions if env var leaks.
- **`exportLinks` for Workspace revisions** — `revisions().get_media()` returns `fileNotDownloadable` for Docs/Sheets/Slides. `files.download()` returns async Operation and doesn't support Slides. `exportLinks` from `revisions.get()` metadata is the only simple path that covers all three.
- **`MediaIoBaseUpload` not `MediaInMemoryUpload`** — latter is deprecated. `MediaIoBaseUpload(io.BytesIO(content))` is identical but future-proof.
- **Pre-revert impact check** — comments detected via Drive `comments.list()`, suggestions via Docs API `documents.get()` walking TextRun elements. Both use existing `google-api-python-client` (no new pip dep). If either exists, requires `--force`.
- **Binary git extraction uses `text=False`** — `subprocess.run` without `text=True` to correctly capture binary content (PDFs, images). `git show` in blobless clones auto-fetches blobs on demand from the remote.
- **`files().update()` with content overwrites Workspace files** — verified: file stays a Google Doc, same ID/URL/permissions. But comments/suggestions ARE destroyed (hence the pre-check).

## Workspace file revert caveats (document for users)
- Comments anchored to text will be lost/orphaned
- Suggestions (proposed edits) will be destroyed
- Some formatting may be lossy (docx round-trip)
- File ID, URL, sharing permissions are preserved
- Drive revisions auto-purge (~30 days for non-pinned) — only recent revisions available

## Stress-test findings

Issues caught and fixed during adversarial review:

| # | Issue | Severity | Resolution |
|---|-------|----------|------------|
| 1 | `revisions().get_media()` doesn't work for Workspace files | **CRITICAL** | Switched to `exportLinks` from revision metadata + `AuthorizedSession` download |
| 2 | `DRIVE_WRITE_ENABLED` config flag could leak to Cloud Functions | HIGH | Eliminated config flag. Scope is constructor-injected: `DriveClient(scope=...)` |
| 3 | `MediaInMemoryUpload` is deprecated | MEDIUM | Switched to `MediaIoBaseUpload(io.BytesIO(content))` |
| 4 | No detection of comment/suggestion loss before revert | MEDIUM | Added `check_comments()` (Drive API) + `check_suggestions()` (Docs API). Requires `--force` if either exists. |
| 5 | `files.download()` considered as alternative | INFO | Ruled out: returns async `Operation`, no `download_media()` variant, doesn't support Slides |
| 6 | Binary content capture could corrupt with `text=True` | LOW | Plan correctly omits `text=True` for `git show` |
| 7 | Git blobless clone blob fetch | LOW | Verified: auto-fetches on demand, works for binary + text |

## Testing
- `tests/test_revert_engine.py` — mock DriveClient/StateManager/subprocess. Cover: path resolution, workspace detection, version listing (merged sources), revert by revision (both binary + workspace paths), revert by commit (including workspace file rejection), batch revert by time, dry-run behavior, destructive impact check, error handling for empty exportLinks
- `tests/test_cli.py` — argument parsing, --force flag for workspace files with comments/suggestions, --dry-run propagation
- Drive client tests for new methods: `list_revisions`, `download_revision` (both code paths), `update_file_content`, `check_comments`, `check_suggestions`

## Verification
1. `make lint typecheck` — passes with new modules
2. `make test` — new + existing tests pass, coverage >= 60%
3. Manual: set env vars, run `python functions/cli.py revisions "some/file"`, verify output
4. Manual: run `python functions/cli.py revert "some/file" --to-revision <id> --dry-run`, verify dry-run output
5. Manual: revert a Workspace file with comments, verify `--force` is required
