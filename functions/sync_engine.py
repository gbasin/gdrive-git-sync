"""Core sync orchestration: classify changes, extract text, group by author, commit."""

import logging
import os
import tempfile
from dataclasses import dataclass, field

from googleapiclient.errors import HttpError

from config import get_config
from drive_client import FOLDER_MIME, SHORTCUT_MIME, DriveClient
from git_ops import GitRepo
from state_manager import StateManager
from text_extractor import GOOGLE_NATIVE_EXPORTS as NATIVE_EXPORTS
from text_extractor import extract_text, get_extracted_filename

logger = logging.getLogger(__name__)


def _git_paths(logical_path: str, name: str, mime_type: str) -> tuple[str, str | None]:
    """Convert Drive logical path to (original_git_path, extracted_git_path).

    For Google-native files, original includes the export extension.
    """
    dir_part = os.path.dirname(logical_path)
    if mime_type in NATIVE_EXPORTS:
        _, ext, _ = NATIVE_EXPORTS[mime_type]
        original = os.path.join(dir_part, name + ext) if dir_part else name + ext
    else:
        original = logical_path
    extracted_name = get_extracted_filename(name, mime_type or None)
    extracted = (os.path.join(dir_part, extracted_name) if dir_part else extracted_name) if extracted_name else None
    return original, extracted


class ChangeType:
    ADD = "add"
    MODIFY = "modify"
    RENAME = "rename"
    MOVE = "move"
    DELETE = "delete"
    SKIP = "skip"


@dataclass
class Change:
    file_id: str
    change_type: str
    file_data: dict | None = None
    old_path: str | None = None
    new_path: str | None = None
    author_name: str | None = None
    author_email: str | None = None


@dataclass
class AuthorCommit:
    author_name: str
    author_email: str
    message: str
    files: list["Change"] = field(default_factory=list)


def _resolve_docs_subdir(drive: DriveClient, cfg) -> None:
    """If DOCS_SUBDIR is not explicitly set, default to the Drive folder name."""
    if cfg.docs_subdir:
        return  # explicitly configured — leave it alone
    folder_name = drive.get_folder_name(cfg.drive_folder_id)
    if folder_name:
        cfg.docs_subdir = folder_name
        logger.info(f"Using Drive folder name as subdir: {folder_name}")
    else:
        logger.warning("Could not resolve Drive folder name — files will sync to repo root")


def run_initial_sync(
    drive: DriveClient,
    state: StateManager,
    repo: GitRepo,
    *,
    force: bool = False,
) -> dict[str, object]:
    """Full folder listing + download for existing Drive files.

    Unlike ``run_sync`` (which uses the delta/changes feed), this walks
    every file via ``files.list`` so it works even when the change page
    token was *just* captured (delta is empty).

    Idempotency: each file is compared against Firestore state before
    downloading.  Binary files are matched on ``md5Checksum``; Google-
    native files (Docs/Sheets/Slides) are matched on ``modifiedTime``
    because re-exporting them produces byte-different output even when
    the content hasn't changed.

    Returns a dict with 'count' (files synced) and 'debug' diagnostics.
    """
    cfg = get_config()
    _resolve_docs_subdir(drive, cfg)
    debug: dict[str, object] = {"folder_id": cfg.drive_folder_id}

    all_files = drive.list_all_files()
    debug["files_listed"] = len(all_files)

    if force:
        logger.info("Force flag set — clearing tracked file state")
        state.clear_all_files()

    if not all_files and not force:
        logger.info("No files found in Drive folder")
        return {"count": 0, "debug": debug}

    # Build expected git paths from Drive listing (for orphan cleanup)
    expected_git_paths: set[str] = set()

    # Build Change objects, skipping files already tracked in Firestore
    changes: list[Change] = []
    already_tracked = 0
    for file_data in all_files:
        file_id = file_data["id"]

        # Resolve relative path
        rel_path = drive.get_file_path(file_data)

        # Apply filters
        if drive.matches_exclude_pattern(rel_path):
            logger.debug(f"Excluding {rel_path}")
            continue
        skip_reason = drive.should_skip_file(file_data)
        if skip_reason:
            logger.info(f"Skipping {rel_path}: {skip_reason}")
            continue

        # Track expected git paths for orphan cleanup
        name = file_data.get("name", "")
        mime_type = file_data.get("mimeType", "")
        original, extracted = _git_paths(rel_path, name, mime_type)
        full_original = os.path.join(cfg.docs_subdir, original) if cfg.docs_subdir else original
        expected_git_paths.add(full_original)
        if extracted:
            full_extracted = os.path.join(cfg.docs_subdir, extracted) if cfg.docs_subdir else extracted
            expected_git_paths.add(full_extracted)

        # Idempotency: check if already tracked with same content
        existing = state.get_file(file_id)
        if existing:
            md5 = file_data.get("md5Checksum")
            if md5:
                if md5 == existing.get("md5"):
                    already_tracked += 1
                    continue
            else:
                # Google-native file — compare modifiedTime
                if file_data.get("modifiedTime") == existing.get("modified_time"):
                    already_tracked += 1
                    continue

        last_user = file_data.get("lastModifyingUser", {})
        changes.append(
            Change(
                file_id=file_id,
                change_type=ChangeType.ADD,
                file_data=file_data,
                new_path=rel_path,
                author_name=last_user.get("displayName"),
                author_email=last_user.get("emailAddress"),
            )
        )

    # Clone (handles empty repos) — needed for both changes and orphan cleanup
    repo.clone_or_init()

    if not changes and not force:
        logger.info("All files already tracked — nothing to sync")
        debug["already_tracked"] = already_tracked
        return {"count": 0, "debug": debug}

    logger.info(f"Initial sync: {len(changes)} files to process, {already_tracked} already tracked")

    processed, had_failures = process_changes(changes, drive, repo, state, cfg)
    if had_failures:
        debug["had_failures"] = True
        debug["failed_count"] = len(changes) - len(processed)

    if processed:
        author_groups = group_by_author(processed, cfg)

        # Override commit messages for initial sync
        for group in author_groups:
            descriptions = []
            for change in group.files:
                descriptions.append(f"  - {change.new_path}")
            group.message = "Initial sync from Google Drive\n\n" + "\n".join(descriptions)

        if len(author_groups) == 1:
            group = author_groups[0]
            if repo.has_staged_changes():
                repo.commit(group.message, group.author_name, group.author_email)
        else:
            repo.unstage_all()
            for group in author_groups:
                for change in group.files:
                    _stage_change_files(change, repo, cfg.docs_subdir, state)
                if repo.has_staged_changes():
                    repo.commit(group.message, group.author_name, group.author_email)

    # Orphan cleanup: remove git files not found in Drive listing
    orphans_removed = 0
    if force and expected_git_paths:
        tracked_files = repo.list_tracked_files()
        for git_path in tracked_files:
            if git_path not in expected_git_paths:
                repo.delete_file(git_path)
                orphans_removed += 1
                logger.info(f"Removing orphaned file: {git_path}")
        if orphans_removed:
            repo.stage_file(".")
            if repo.has_staged_changes():
                repo.commit(
                    f"Remove {orphans_removed} orphaned files not in Drive",
                    cfg.commit_author_name,
                    cfg.commit_author_email,
                )
            debug["orphans_removed"] = orphans_removed
            # Clean up Firestore: remove state entries whose paths don't match Drive
            all_state = state.get_all_files()
            for file_id, data in all_state.items():
                path = data.get("path", "")
                full_path = os.path.join(cfg.docs_subdir, path) if cfg.docs_subdir else path
                if full_path not in expected_git_paths:
                    state.delete_file(file_id)

    repo.push_if_ahead()

    # Update Firestore state after push (or after confirming remote
    # is already up to date).  If push_if_ahead() raises, this loop
    # is skipped — the next run will re-download and retry.
    for change in processed:
        update_file_state(change, state)

    logger.info(f"Initial sync complete: {len(processed)} files synced, {orphans_removed} orphans removed")
    debug["files_synced"] = len(processed)
    return {"count": len(processed), "debug": debug}


def run_sync(drive: DriveClient, state: StateManager, repo: GitRepo) -> int:
    """Execute a full sync cycle. Returns number of changes processed."""
    cfg = get_config()
    _resolve_docs_subdir(drive, cfg)

    # Get current page token
    page_token = state.get_page_token()
    if page_token is None:
        logger.info("No page token found — getting initial token")
        page_token = drive.get_start_page_token()
        state.set_page_token(page_token)
        return 0

    # Fetch changes
    raw_changes, new_token = drive.list_changes(page_token)
    if not raw_changes:
        state.set_page_token(new_token)
        logger.info("No changes found")
        return 0

    logger.info(f"Found {len(raw_changes)} raw changes")

    # Deduplicate: keep latest change per fileId
    deduped: dict[str, dict] = {}
    for raw_item in raw_changes:
        file_id = raw_item.get("fileId")
        if file_id:
            deduped[file_id] = raw_item

    # Classify each change
    changes: list[Change] = []
    for file_id, raw in deduped.items():
        result = classify_change(file_id, raw, drive, state)
        if result is None:
            continue
        if isinstance(result, list):
            changes.extend(c for c in result if c.change_type != ChangeType.SKIP)
        elif result.change_type != ChangeType.SKIP:
            changes.append(result)

    # Dedup: prefer direct changes (with file_data) over synthetic MOVEs/DELETEs
    seen: dict[str, Change] = {}
    for c in changes:
        if c.file_id in seen:
            if c.file_data is not None:
                seen[c.file_id] = c
        else:
            seen[c.file_id] = c
    changes = list(seen.values())

    if not changes:
        state.set_page_token(new_token)
        logger.info("All changes were skipped")
        return 0

    # Never process deletes from the changes API — it reports false
    # "removed" signals for files in shared folders (service-account
    # blindness).  Deletes are handled exclusively by diff sync, which
    # uses the reliable files.list API and verifies each delete via
    # files.get() before processing.
    delete_changes = [c for c in changes if c.change_type == ChangeType.DELETE]
    if delete_changes:
        logger.info(f"Dropping {len(delete_changes)} delete(s) from webhook — deletes handled by diff sync only")
        changes = [c for c in changes if c.change_type != ChangeType.DELETE]
        if not changes:
            state.set_page_token(new_token)
            return 0

    logger.info(f"Processing {len(changes)} changes")

    # Clone repo (clone_or_init handles empty repos gracefully)
    repo.clone_or_init()

    # Process changes: download files, extract text
    # Files are written to repo working tree and staged via git add
    processed, had_failures = process_changes(changes, drive, repo, state, cfg)

    if processed:
        author_groups = group_by_author(processed, cfg)

        if len(author_groups) == 1:
            # Single author — commit all staged changes
            group = author_groups[0]
            if repo.has_staged_changes():
                repo.commit(group.message, group.author_name, group.author_email)
        else:
            # Multiple authors — unstage all, then re-stage per author
            repo.unstage_all()
            for group in author_groups:
                for change in group.files:
                    _stage_change_files(change, repo, cfg.docs_subdir, state)
                if repo.has_staged_changes():
                    repo.commit(group.message, group.author_name, group.author_email)

        repo.push()

        # Update Firestore state after successful push
        for change in processed:
            update_file_state(change, state)

    # Only advance page token if all changes were processed successfully.
    # On partial failure the old token is kept so failed changes are retried.
    if not had_failures:
        state.set_page_token(new_token)
    else:
        logger.warning("Partial failures — keeping old page token for retry")
    logger.info(f"Sync complete: {len(processed)} changes committed")
    return len(processed)


def run_diff_sync(drive: DriveClient, state: StateManager, repo: GitRepo) -> int:
    """Sync by comparing full Drive listing against Firestore state.

    Fallback for when ``changes.list`` returns nothing — e.g. a service
    account whose change feed doesn't include files in a shared folder.
    Detects ADD, MODIFY, DELETE, and RENAME/MOVE by diffing the live
    listing against persisted state.
    """
    cfg = get_config()
    _resolve_docs_subdir(drive, cfg)

    all_files = drive.list_all_files()
    all_state = state.get_all_files()

    if not all_files and not all_state:
        logger.info("Diff sync: no files in Drive or state")
        return 0

    # Safety guard: if Drive returns nothing but we have tracked files,
    # the listing is almost certainly incomplete — skip to avoid mass deletes.
    if not all_files and all_state:
        logger.warning(
            f"Diff sync: Drive listing returned 0 files but {len(all_state)} "
            "are tracked — skipping to avoid false deletes"
        )
        return 0

    # Build map of file_id → (file_data, rel_path) for Drive listing
    drive_map: dict[str, tuple[dict, str]] = {}
    for f in all_files:
        rel_path = drive.get_file_path(f)
        if drive.matches_exclude_pattern(rel_path):
            continue
        if drive.should_skip_file(f):
            continue
        drive_map[f["id"]] = (f, rel_path)

    changes: list[Change] = []

    # Use cached all_state for lookups (single Firestore read) instead
    # of individual get_file() calls for consistency and performance.
    for file_id, (file_data, rel_path) in drive_map.items():
        existing = all_state.get(file_id)
        last_user = file_data.get("lastModifyingUser", {})
        author_name = last_user.get("displayName")
        author_email = last_user.get("emailAddress")

        if not existing:
            # New file
            changes.append(
                Change(
                    file_id=file_id,
                    change_type=ChangeType.ADD,
                    file_data=file_data,
                    new_path=rel_path,
                    author_name=author_name,
                    author_email=author_email,
                )
            )
            continue

        old_path = existing.get("path")

        # Rename or move
        if old_path and old_path != rel_path:
            old_name = existing.get("name")
            new_name = file_data.get("name")
            change_type = ChangeType.RENAME if old_name != new_name else ChangeType.MOVE
            changes.append(
                Change(
                    file_id=file_id,
                    change_type=change_type,
                    file_data=file_data,
                    old_path=old_path,
                    new_path=rel_path,
                    author_name=author_name,
                    author_email=author_email,
                )
            )
            continue

        # Content change
        md5 = file_data.get("md5Checksum")
        if md5:
            if md5 != existing.get("md5"):
                changes.append(
                    Change(
                        file_id=file_id,
                        change_type=ChangeType.MODIFY,
                        file_data=file_data,
                        new_path=rel_path,
                        author_name=author_name,
                        author_email=author_email,
                    )
                )
        else:
            # Google-native file — compare modifiedTime
            if file_data.get("modifiedTime") != existing.get("modified_time"):
                changes.append(
                    Change(
                        file_id=file_id,
                        change_type=ChangeType.MODIFY,
                        file_data=file_data,
                        new_path=rel_path,
                        author_name=author_name,
                        author_email=author_email,
                    )
                )

    # Detect deletes: files in Firestore but not in Drive listing.
    # Verify each candidate via files.get() to avoid false deletes from
    # incomplete listings.
    skipped_deletes = 0
    for file_id, data in all_state.items():
        if file_id not in drive_map:
            if drive.verify_file_deleted(file_id):
                changes.append(
                    Change(
                        file_id=file_id,
                        change_type=ChangeType.DELETE,
                        old_path=data.get("path"),
                    )
                )
            else:
                skipped_deletes += 1
    if skipped_deletes:
        logger.info(f"Diff sync: skipped {skipped_deletes} delete(s) — files still exist per files.get()")

    if not changes:
        logger.info("Diff sync: no changes detected")
        return 0

    logger.info(f"Diff sync: {len(changes)} changes detected")

    repo.clone_or_init()
    processed, had_failures = process_changes(changes, drive, repo, state, cfg)

    if processed:
        author_groups = group_by_author(processed, cfg)

        if len(author_groups) == 1:
            group = author_groups[0]
            if repo.has_staged_changes():
                repo.commit(group.message, group.author_name, group.author_email)
        else:
            repo.unstage_all()
            for group in author_groups:
                for change in group.files:
                    _stage_change_files(change, repo, cfg.docs_subdir, state)
                if repo.has_staged_changes():
                    repo.commit(group.message, group.author_name, group.author_email)

        repo.push()

        for change in processed:
            update_file_state(change, state)

    # Advance page token so the next run_sync doesn't re-examine stale deltas
    try:
        state.set_page_token(drive.get_start_page_token())
    except Exception:
        logger.warning("Could not advance page token after diff sync", exc_info=True)

    if had_failures:
        logger.warning(f"Diff sync: {len(changes) - len(processed)} failures")

    logger.info(f"Diff sync complete: {len(processed)} changes committed")
    return len(processed)


def classify_change(file_id: str, raw: dict, drive: DriveClient, state: StateManager) -> Change | list[Change] | None:
    """Classify a Drive change into an action type."""
    removed = raw.get("removed", False)
    file_data = raw.get("file", {})
    trashed = file_data.get("trashed", False)
    existing = state.get_file(file_id)

    # File removed or trashed — check before resolving shortcuts to avoid
    # a wasted API call fetching the target of a deleted shortcut.
    if removed or trashed:
        if existing:
            return Change(
                file_id=file_id,
                change_type=ChangeType.DELETE,
                old_path=existing.get("path"),
            )
        # Target file for a tracked shortcut may be deleted/trashed.
        result = state.get_file_by_target(file_id)
        if result:
            shortcut_id, shortcut_state = result
            return Change(
                file_id=shortcut_id,
                change_type=ChangeType.DELETE,
                old_path=shortcut_state.get("path"),
            )
        return None  # Unknown file deleted, nothing to do

    # Folders — expand to child file changes (renames, moves, deletes)
    if file_data.get("mimeType") == FOLDER_MIME:
        return _handle_folder_change(file_id, file_data, drive, state)

    # Resolve shortcuts — replace file_data with target metadata
    if file_data.get("mimeType") == SHORTCUT_MIME:
        resolved = drive.resolve_shortcut(file_data)
        if not resolved:
            logger.warning(f"Broken shortcut {file_id} — skipping")
            return None
        file_data = resolved

    # Check if file is in monitored folder
    if not drive.is_in_folder(file_data):
        if existing:
            # File moved out of our folder → delete
            return Change(
                file_id=file_id,
                change_type=ChangeType.DELETE,
                old_path=existing.get("path"),
            )
        # Check if this is a shortcut target we're tracking
        result = state.get_file_by_target(file_id)
        if result:
            shortcut_id, shortcut_state = result
            merged = dict(file_data)
            merged["id"] = shortcut_id
            shortcut_name = shortcut_state.get("name")
            if not shortcut_name:
                shortcut_path = shortcut_state.get("path", "")
                shortcut_name = os.path.basename(shortcut_path) if shortcut_path else ""
            if shortcut_name:
                merged["name"] = shortcut_name
            merged["_target_id"] = file_id
            return Change(
                file_id=shortcut_id,
                change_type=ChangeType.MODIFY,
                file_data=merged,
                new_path=shortcut_state.get("path"),
                author_name=file_data.get("lastModifyingUser", {}).get("displayName"),
                author_email=file_data.get("lastModifyingUser", {}).get("emailAddress"),
            )
        return None  # Not our file

    # Get relative path
    rel_path = drive.get_file_path(file_data)

    # Check exclude patterns
    if drive.matches_exclude_pattern(rel_path):
        logger.debug(f"Excluding {rel_path}")
        return None

    # Check skip conditions
    skip_reason = drive.should_skip_file(file_data)
    if skip_reason:
        logger.info(f"Skipping {rel_path}: {skip_reason}")
        return None

    # Extract author info
    last_user = file_data.get("lastModifyingUser", {})
    author_name = last_user.get("displayName")
    author_email = last_user.get("emailAddress")

    # New file
    if not existing:
        return Change(
            file_id=file_id,
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path=rel_path,
            author_name=author_name,
            author_email=author_email,
        )

    old_path = existing.get("path")

    # Rename or move detection
    if old_path != rel_path:
        old_name = existing.get("name")
        new_name = file_data.get("name")
        change_type = ChangeType.RENAME if old_name != new_name else ChangeType.MOVE

        return Change(
            file_id=file_id,
            change_type=change_type,
            file_data=file_data,
            old_path=old_path,
            new_path=rel_path,
            author_name=author_name,
            author_email=author_email,
        )

    # Content change detection
    md5 = file_data.get("md5Checksum")
    if md5:
        # Binary file — use md5
        if md5 != existing.get("md5"):
            return Change(
                file_id=file_id,
                change_type=ChangeType.MODIFY,
                file_data=file_data,
                new_path=rel_path,
                author_name=author_name,
                author_email=author_email,
            )
    else:
        # Google-native file — use modifiedTime
        modified = file_data.get("modifiedTime")
        if modified != existing.get("modified_time"):
            return Change(
                file_id=file_id,
                change_type=ChangeType.MODIFY,
                file_data=file_data,
                new_path=rel_path,
                author_name=author_name,
                author_email=author_email,
            )

    return Change(file_id=file_id, change_type=ChangeType.SKIP)


def _handle_folder_change(
    folder_id: str, file_data: dict, drive: DriveClient, state: StateManager
) -> list[Change] | None:
    """Expand a folder change into child file moves or deletes."""
    if not drive.is_in_folder(file_data):
        return _cascade_folder_delete(folder_id, file_data, drive, state)

    children = drive.list_folder_files(folder_id)
    if not children:
        logger.debug(f"Folder {file_data.get('name')} — no children to move")
        return []

    last_user = file_data.get("lastModifyingUser", {})
    changes: list[Change] = []
    for child in children:
        child_id = child["id"]
        existing = state.get_file(child_id)
        if not existing:
            continue
        old_path = existing.get("path")
        new_path = drive.get_file_path(child)
        if old_path and new_path and old_path != new_path:
            changes.append(
                Change(
                    file_id=child_id,
                    change_type=ChangeType.MOVE,
                    file_data=None,
                    old_path=old_path,
                    new_path=new_path,
                    author_name=last_user.get("displayName"),
                    author_email=last_user.get("emailAddress"),
                )
            )
    if changes:
        logger.info(f"Folder rename expanded to {len(changes)} file moves")
    else:
        logger.debug(f"Folder {file_data.get('name')} — no tracked files with changed paths")
    return changes


def _cascade_folder_delete(
    folder_id: str, file_data: dict | None, drive: DriveClient, state: StateManager
) -> list[Change]:
    """Folder moved out of monitored tree — delete all tracked children."""
    children = drive.list_folder_files(folder_id)
    changes: list[Change] = []
    if children:
        for child in children:
            existing = state.get_file(child["id"])
            if existing:
                changes.append(
                    Change(
                        file_id=child["id"],
                        change_type=ChangeType.DELETE,
                        old_path=existing.get("path"),
                    )
                )
    else:
        # Drive API couldn't list children (trashed folder, permission issue).
        # Fall back to Firestore state: find tracked files under this folder.
        # We match by folder name as a path component.  To avoid over-deletion
        # when multiple folders share the same name, we require all matches
        # to share a single common prefix (e.g. "A/MyFolder/").  If matches
        # span multiple distinct prefixes, we skip to avoid data loss.
        folder_name = file_data.get("name") if file_data else None
        if folder_name:
            all_tracked = state.get_all_files()
            candidates: dict[str, str] = {}  # file_id → path
            for fid, fdata in all_tracked.items():
                p = fdata.get("path", "")
                if p.startswith(folder_name + "/") or ("/" + folder_name + "/") in p:
                    candidates[fid] = p

            # Check for ambiguity: extract the prefix up to and including folder_name
            prefixes: set[str] = set()
            for p in candidates.values():
                idx = p.find(folder_name + "/")
                prefixes.add(p[: idx + len(folder_name) + 1])

            if len(prefixes) > 1:
                logger.warning(
                    f"Folder {folder_name} matches {len(prefixes)} distinct paths — "
                    f"skipping fallback deletion to avoid ambiguity: {prefixes}"
                )
            elif candidates:
                for fid, p in candidates.items():
                    changes.append(Change(file_id=fid, change_type=ChangeType.DELETE, old_path=p))
                logger.info(f"Folder {folder_name} — used state fallback, {len(changes)} tracked files")
        if not changes:
            logger.debug(f"Folder {folder_id} not accessible or empty")
    if changes:
        logger.info(f"Folder moved out — deleting {len(changes)} tracked files")
    return changes


def process_changes(
    changes: list[Change],
    drive: DriveClient,
    repo: GitRepo,
    state: StateManager,
    cfg,
) -> tuple[list[Change], bool]:
    """Download, extract, and stage files for each change.

    Returns (processed_changes, had_failures).
    """
    processed = []
    had_failures = False
    docs_subdir = cfg.docs_subdir

    for change in changes:
        try:
            if change.change_type == ChangeType.DELETE:
                _handle_delete(change, repo, state, docs_subdir)
                processed.append(change)

            elif change.change_type in (ChangeType.RENAME, ChangeType.MOVE):
                _handle_rename(change, drive, repo, state, docs_subdir)
                processed.append(change)

            elif change.change_type in (ChangeType.ADD, ChangeType.MODIFY):
                _handle_add_or_modify(change, drive, repo, docs_subdir)
                processed.append(change)

        except Exception:
            logger.exception(f"Failed to process {change.change_type} for {change.file_id}")
            had_failures = True

    return processed, had_failures


def _handle_delete(change: Change, repo: GitRepo, state: StateManager, docs_subdir: str):
    """Delete both original and extracted files."""
    if not change.old_path:
        return

    existing = state.get_file(change.file_id)
    name = existing.get("name", os.path.basename(change.old_path)) if existing else os.path.basename(change.old_path)
    mime_type = existing.get("mime_type", "") if existing else ""
    original, extracted = _git_paths(change.old_path, name, mime_type)

    repo.delete_file(os.path.join(docs_subdir, original))
    if extracted:
        repo.delete_file(os.path.join(docs_subdir, extracted))

    logger.info(f"Deleted {change.old_path}")


def _handle_rename(change: Change, drive: DriveClient, repo: GitRepo, state: StateManager, docs_subdir: str):
    """Rename/move files using git mv, then update content if also modified."""
    if not change.old_path or not change.new_path:
        return

    existing = state.get_file(change.file_id)

    if change.file_data:
        mime_type = change.file_data.get("mimeType", "")
        new_name = change.file_data.get("name", "")
    elif existing:
        mime_type = existing.get("mime_type", "")
        new_name = existing.get("name", "")
    else:
        mime_type = ""
        new_name = os.path.basename(change.new_path)

    old_name = (
        existing.get("name", os.path.basename(change.old_path)) if existing else os.path.basename(change.old_path)
    )

    old_original, old_extracted = _git_paths(change.old_path, old_name, mime_type)
    new_original, new_extracted = _git_paths(change.new_path, new_name, mime_type)

    moved_ok = repo.rename_file(os.path.join(docs_subdir, old_original), os.path.join(docs_subdir, new_original))
    if old_extracted and new_extracted:
        repo.rename_file(os.path.join(docs_subdir, old_extracted), os.path.join(docs_subdir, new_extracted))

    # Re-download if content changed, OR if source file was missing (rename skipped)
    if change.file_data:
        need_download = not moved_ok  # source missing — re-download to new path
        if not need_download and existing:
            md5 = change.file_data.get("md5Checksum")
            old_md5 = existing.get("md5")
            need_download = (md5 and md5 != old_md5) or (
                not md5 and change.file_data.get("modifiedTime") != existing.get("modified_time")
            )
        if need_download:
            _download_and_extract(change, drive, repo, docs_subdir)

    logger.info(f"Renamed {change.old_path} → {change.new_path}")


def _handle_add_or_modify(change: Change, drive: DriveClient, repo: GitRepo, docs_subdir: str):
    """Download file and extract text."""
    _download_and_extract(change, drive, repo, docs_subdir)
    logger.info(f"{change.change_type.upper()} {change.new_path}")


def _is_not_downloadable(exc: Exception) -> bool:
    """Check if an exception is a 403 fileNotDownloadable from the Drive API."""
    return (
        isinstance(exc, HttpError)
        and exc.status_code == 403
        and any(d.get("reason") == "fileNotDownloadable" for d in (exc.error_details or []))
    )


def _download_and_extract(change: Change, drive: DriveClient, repo: GitRepo, docs_subdir: str):
    """Download a file from Drive, write original to repo, extract text alongside it."""
    file_data = change.file_data
    assert file_data is not None
    assert change.new_path is not None
    mime_type = file_data.get("mimeType", "")
    name = file_data.get("name", "unknown")
    download_id = file_data.get("_target_id", change.file_id)

    logger.info(f"Downloading {name} (id={download_id}, mimeType={mime_type})")

    # Determine if Google-native and needs export
    if mime_type in NATIVE_EXPORTS:
        fmt, ext, export_mime = NATIVE_EXPORTS[mime_type]
        content = drive.export_file(download_id, export_mime)
        # For Google-native, the "original" is the export (e.g., .docx, .csv)
        exported_name = name + ext
        dir_part = os.path.dirname(change.new_path) if "/" in change.new_path else ""
        original_path = os.path.join(dir_part, exported_name) if dir_part else exported_name
    else:
        try:
            content = drive.download_file(download_id)
        except Exception as exc:
            # If Drive says the file isn't downloadable, it's a Google-native
            # file whose mimeType wasn't in the changes feed metadata.
            # Re-fetch the actual mimeType and retry as an export.
            if not _is_not_downloadable(exc):
                raise
            actual_mime = drive.get_file_mime(download_id)
            logger.warning(
                f"download_file failed for {name}: mimeType was '{mime_type}', "
                f"actual mimeType is '{actual_mime}' — retrying as export"
            )
            if not actual_mime or actual_mime not in NATIVE_EXPORTS:
                raise
            mime_type = actual_mime
            fmt, ext, export_mime = NATIVE_EXPORTS[mime_type]
            content = drive.export_file(download_id, export_mime)
            exported_name = name + ext
            dir_part = os.path.dirname(change.new_path) if "/" in change.new_path else ""
            original_path = os.path.join(dir_part, exported_name) if dir_part else exported_name
            # Update file_data so downstream text extraction and state
            # recording use the correct mimeType.
            file_data["mimeType"] = actual_mime
        else:
            original_path = change.new_path

    # Write original to repo
    repo.write_file(os.path.join(docs_subdir, original_path), content)

    # Extract text
    extracted_name = get_extracted_filename(name, mime_type)
    if extracted_name:
        dir_part = os.path.dirname(change.new_path) if "/" in change.new_path else ""
        extracted_rel = os.path.join(dir_part, extracted_name) if dir_part else extracted_name

        # Write to temp file for extraction
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(original_path)[1], delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            extracted_tmp = tmp_path + ".extracted"
            if extract_text(tmp_path, extracted_tmp, mime_type):
                with open(extracted_tmp, "rb") as f:
                    repo.write_file(os.path.join(docs_subdir, extracted_rel), f.read())
                os.unlink(extracted_tmp)
        finally:
            os.unlink(tmp_path)


def group_by_author(changes: list[Change], cfg) -> list[AuthorCommit]:
    """Group changes by author for separate commits."""
    groups: dict[str, AuthorCommit] = {}

    for change in changes:
        author_name = change.author_name or cfg.commit_author_name
        author_email = change.author_email or cfg.commit_author_email
        key = f"{author_name} <{author_email}>"

        if key not in groups:
            groups[key] = AuthorCommit(
                author_name=author_name,
                author_email=author_email,
                message="",
                files=[],
            )

        groups[key].files.append(change)

    # Build commit messages
    for group in groups.values():
        descriptions = []
        for change in group.files:
            path = change.new_path or change.old_path or change.file_id
            descriptions.append(f"  - {change.change_type}: {path}")

        group.message = "Sync from Google Drive\n\n" + "\n".join(descriptions)

    return list(groups.values())


def _stage_change_files(change: Change, repo: GitRepo, docs_subdir: str, state: StateManager | None = None):
    """Stage the files associated with a change (for multi-author commits)."""
    if change.change_type == ChangeType.DELETE:
        if not change.old_path:
            return
        existing = state.get_file(change.file_id) if state else None
        name = (
            existing.get("name", os.path.basename(change.old_path)) if existing else os.path.basename(change.old_path)
        )
        mime_type = existing.get("mime_type", "") if existing else ""
        original, extracted = _git_paths(change.old_path, name, mime_type)
        repo.stage_file(os.path.join(docs_subdir, original), ignore_missing=True)
        if extracted:
            repo.stage_file(os.path.join(docs_subdir, extracted), ignore_missing=True)

    elif change.change_type in (ChangeType.RENAME, ChangeType.MOVE):
        existing = state.get_file(change.file_id) if state else None
        mime_type = (
            change.file_data.get("mimeType", "")
            if change.file_data
            else (existing.get("mime_type", "") if existing else "")
        )
        old_name = existing.get("name", "") if existing else os.path.basename(change.old_path or "")
        new_name = (
            change.file_data.get("name", "") if change.file_data else (existing.get("name", "") if existing else "")
        )
        if change.old_path:
            old_orig, old_ext = _git_paths(change.old_path, old_name, mime_type)
            repo.stage_file(os.path.join(docs_subdir, old_orig), ignore_missing=True)
            if old_ext:
                repo.stage_file(os.path.join(docs_subdir, old_ext), ignore_missing=True)
        if change.new_path:
            new_orig, new_ext = _git_paths(change.new_path, new_name, mime_type)
            repo.stage_file(os.path.join(docs_subdir, new_orig))
            if new_ext:
                repo.stage_file(os.path.join(docs_subdir, new_ext))

    else:
        if change.new_path and change.file_data:
            # For Google-native files, _download_and_extract writes to
            # name + export extension (e.g. "My Doc.docx"), not new_path
            # ("My Doc").  Compute the actual path so we stage the right file.
            mime_type = change.file_data.get("mimeType", "")
            name = change.file_data.get("name", "")
            original, extracted = _git_paths(change.new_path, name, mime_type)
            repo.stage_file(os.path.join(docs_subdir, original))
            if extracted:
                repo.stage_file(os.path.join(docs_subdir, extracted))
        elif change.new_path:
            repo.stage_file(os.path.join(docs_subdir, change.new_path))


def update_file_state(change: Change, state: StateManager):
    """Update Firestore file tracking after successful commit."""
    if change.change_type == ChangeType.DELETE:
        state.delete_file(change.file_id)
        return

    file_data = change.file_data or {}

    if change.change_type in (ChangeType.RENAME, ChangeType.MOVE):
        existing = state.get_file(change.file_id) or {}
        state_data = dict(existing)
        state_data["path"] = change.new_path or existing.get("path", "")
        if file_data.get("name"):
            state_data["name"] = file_data["name"]
        # Recompute extracted_path
        name = state_data.get("name", "")
        mime_type = file_data.get("mimeType") or state_data.get("mime_type", "")
        extracted_name = get_extracted_filename(name, mime_type)
        dir_part = os.path.dirname(state_data["path"])
        state_data["extracted_path"] = (
            (os.path.join(dir_part, extracted_name) if dir_part else extracted_name) if extracted_name else None
        )
        if file_data.get("md5Checksum"):
            state_data["md5"] = file_data["md5Checksum"]
        if file_data.get("modifiedTime"):
            state_data["modified_time"] = file_data["modifiedTime"]
        if file_data.get("mimeType"):
            state_data["mime_type"] = file_data["mimeType"]
        last_user = file_data.get("lastModifyingUser", {})
        if last_user.get("displayName"):
            state_data["last_modified_by_name"] = last_user["displayName"]
            state_data["last_modified_by_email"] = last_user.get("emailAddress")
        state.set_file(change.file_id, state_data)
        return

    # ADD/MODIFY — full-write logic
    name = file_data.get("name", "")
    mime_type = file_data.get("mimeType", "")
    path = change.new_path or ""
    extracted_name = get_extracted_filename(name, mime_type)
    dir_part = os.path.dirname(path) if "/" in path else ""
    extracted_path = os.path.join(dir_part, extracted_name) if (extracted_name and dir_part) else extracted_name

    last_user = file_data.get("lastModifyingUser", {})

    state_data = {
        "name": name,
        "path": path,
        "md5": file_data.get("md5Checksum"),
        "mime_type": mime_type,
        "modified_time": file_data.get("modifiedTime"),
        "extracted_path": extracted_path,
        "last_modified_by_name": last_user.get("displayName"),
        "last_modified_by_email": last_user.get("emailAddress"),
    }
    target_id = file_data.get("_target_id")
    if target_id:
        state_data["target_id"] = target_id

    state.set_file(change.file_id, state_data)
