"""Core sync orchestration: classify changes, extract text, group by author, commit."""

import logging
import os
import tempfile
from dataclasses import dataclass, field

from config import get_config
from drive_client import DriveClient
from git_ops import GitRepo
from state_manager import StateManager
from text_extractor import GOOGLE_NATIVE_EXPORTS as NATIVE_EXPORTS
from text_extractor import extract_text, get_extracted_filename

logger = logging.getLogger(__name__)


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
    files: list[tuple[str, bytes | None]] = field(default_factory=list)
    # (rel_path, content_bytes) — None content means delete


def run_sync(drive: DriveClient, state: StateManager, repo: GitRepo) -> int:
    """Execute a full sync cycle. Returns number of changes processed."""
    cfg = get_config()

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
    for change in raw_changes:
        file_id = change.get("fileId")
        if file_id:
            deduped[file_id] = change

    # Classify each change
    changes = []
    for file_id, raw in deduped.items():
        change = classify_change(file_id, raw, drive, state)
        if change and change.change_type != ChangeType.SKIP:
            changes.append(change)

    if not changes:
        state.set_page_token(new_token)
        logger.info("All changes were skipped")
        return 0

    logger.info(f"Processing {len(changes)} changes")

    # Clone repo
    repo.clone()

    # Process changes: download files, extract text
    # Files are written to repo working tree and staged via git add
    processed = process_changes(changes, drive, repo, state, cfg)

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
                    _stage_change_files(change, repo, cfg.docs_subdir)
                if repo.has_staged_changes():
                    repo.commit(group.message, group.author_name, group.author_email)

        repo.push()

        # Update Firestore state after successful push
        for change in processed:
            update_file_state(change, state)

    # Update page token
    state.set_page_token(new_token)
    logger.info(f"Sync complete: {len(processed)} changes committed")
    return len(processed)


def classify_change(file_id: str, raw: dict, drive: DriveClient, state: StateManager) -> Change | None:
    """Classify a Drive change into an action type."""
    removed = raw.get("removed", False)
    file_data = raw.get("file", {})
    trashed = file_data.get("trashed", False)
    existing = state.get_file(file_id)

    # File removed or trashed
    if removed or trashed:
        if existing:
            return Change(
                file_id=file_id,
                change_type=ChangeType.DELETE,
                old_path=existing.get("path"),
            )
        return None  # Unknown file deleted, nothing to do

    # Check if file is in monitored folder
    if not drive.is_in_folder(file_data):
        if existing:
            # File moved out of our folder → delete
            return Change(
                file_id=file_id,
                change_type=ChangeType.DELETE,
                old_path=existing.get("path"),
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


def process_changes(
    changes: list[Change],
    drive: DriveClient,
    repo: GitRepo,
    state: StateManager,
    cfg,
) -> list[Change]:
    """Download, extract, and stage files for each change. Returns processed changes."""
    processed = []
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

    return processed


def _handle_delete(change: Change, repo: GitRepo, state: StateManager, docs_subdir: str):
    """Delete both original and extracted files."""
    if not change.old_path:
        return

    existing = state.get_file(change.file_id)
    original_rel = os.path.join(docs_subdir, change.old_path)
    repo.delete_file(original_rel)

    # Also delete extracted file if it exists
    if existing and existing.get("extracted_path"):
        extracted_rel = os.path.join(docs_subdir, existing["extracted_path"])
        repo.delete_file(extracted_rel)

    logger.info(f"Deleted {change.old_path}")


def _handle_rename(change: Change, drive: DriveClient, repo: GitRepo, state: StateManager, docs_subdir: str):
    """Rename/move files using git mv, then update content if also modified."""
    old_rel = os.path.join(docs_subdir, change.old_path)
    new_rel = os.path.join(docs_subdir, change.new_path)

    repo.rename_file(old_rel, new_rel)

    # Also rename extracted file
    existing = state.get_file(change.file_id)
    if existing and existing.get("extracted_path"):
        old_extracted = os.path.join(docs_subdir, existing["extracted_path"])
        # Compute new extracted filename
        mime_type = change.file_data.get("mimeType") if change.file_data else None
        new_name = change.file_data.get("name", "") if change.file_data else ""
        new_extracted_name = get_extracted_filename(new_name, mime_type)
        if new_extracted_name:
            # Build new extracted path preserving directory
            new_dir = os.path.dirname(change.new_path)
            new_extracted_path = os.path.join(new_dir, new_extracted_name) if new_dir else new_extracted_name
            new_extracted_rel = os.path.join(docs_subdir, new_extracted_path)
            repo.rename_file(old_extracted, new_extracted_rel)

    # If content also changed, re-download and extract
    if change.file_data:
        md5 = change.file_data.get("md5Checksum")
        if existing:
            old_md5 = existing.get("md5")
            if md5 and md5 != old_md5:
                _download_and_extract(change, drive, repo, docs_subdir)
            elif not md5:
                # Google-native: always re-extract on rename (modifiedTime likely changed)
                _download_and_extract(change, drive, repo, docs_subdir)

    logger.info(f"Renamed {change.old_path} → {change.new_path}")


def _handle_add_or_modify(change: Change, drive: DriveClient, repo: GitRepo, docs_subdir: str):
    """Download file and extract text."""
    _download_and_extract(change, drive, repo, docs_subdir)
    logger.info(f"{change.change_type.upper()} {change.new_path}")


def _download_and_extract(change: Change, drive: DriveClient, repo: GitRepo, docs_subdir: str):
    """Download a file from Drive, write original to repo, extract text alongside it."""
    file_data = change.file_data
    mime_type = file_data.get("mimeType", "")
    name = file_data.get("name", "unknown")

    # Determine if Google-native and needs export
    if mime_type in NATIVE_EXPORTS:
        fmt, ext, export_mime = NATIVE_EXPORTS[mime_type]
        content = drive.export_file(change.file_id, export_mime)
        # For Google-native, the "original" is the export (e.g., .docx, .csv)
        exported_name = name + ext
        dir_part = os.path.dirname(change.new_path) if "/" in change.new_path else ""
        original_path = os.path.join(dir_part, exported_name) if dir_part else exported_name
    else:
        content = drive.download_file(change.file_id)
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
                # Store extracted path on change for state update
                if not hasattr(change, "_extracted_path"):
                    change._extracted_path = extracted_rel
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


def _stage_change_files(change: Change, repo: GitRepo, docs_subdir: str):
    """Stage the files associated with a change (for multi-author commits)."""
    if change.change_type == ChangeType.DELETE:
        if change.old_path:
            repo.stage_file(os.path.join(docs_subdir, change.old_path))
    elif change.change_type in (ChangeType.RENAME, ChangeType.MOVE):
        if change.old_path:
            repo.stage_file(os.path.join(docs_subdir, change.old_path))
        if change.new_path:
            repo.stage_file(os.path.join(docs_subdir, change.new_path))
    else:
        if change.new_path:
            repo.stage_file(os.path.join(docs_subdir, change.new_path))
            # Also stage extracted file
            if change.file_data:
                name = change.file_data.get("name", "")
                mime_type = change.file_data.get("mimeType", "")
                extracted_name = get_extracted_filename(name, mime_type)
                if extracted_name:
                    dir_part = os.path.dirname(change.new_path) if "/" in change.new_path else ""
                    extracted_path = os.path.join(dir_part, extracted_name) if dir_part else extracted_name
                    repo.stage_file(os.path.join(docs_subdir, extracted_path))


def update_file_state(change: Change, state: StateManager):
    """Update Firestore file tracking after successful commit."""
    if change.change_type == ChangeType.DELETE:
        state.delete_file(change.file_id)
        return

    file_data = change.file_data or {}
    name = file_data.get("name", "")
    mime_type = file_data.get("mimeType", "")
    path = change.new_path or ""
    extracted_name = get_extracted_filename(name, mime_type)
    dir_part = os.path.dirname(path) if "/" in path else ""
    extracted_path = os.path.join(dir_part, extracted_name) if (extracted_name and dir_part) else extracted_name

    last_user = file_data.get("lastModifyingUser", {})

    state.set_file(
        change.file_id,
        {
            "name": name,
            "path": path,
            "md5": file_data.get("md5Checksum"),
            "mime_type": mime_type,
            "modified_time": file_data.get("modifiedTime"),
            "extracted_path": extracted_path,
            "last_modified_by_name": last_user.get("displayName"),
            "last_modified_by_email": last_user.get("emailAddress"),
        },
    )
