"""Tests for functions/sync_engine.py.

DriveClient, StateManager, and GitRepo are mocked throughout.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from config import reset_config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    """Provide required environment variables for Config."""
    monkeypatch.setenv("GCP_PROJECT", "test-project")
    monkeypatch.setenv("DRIVE_FOLDER_ID", "folder123")
    monkeypatch.setenv("GIT_REPO_URL", "https://github.com/test/repo.git")
    monkeypatch.setenv("GIT_BRANCH", "main")
    monkeypatch.setenv("GIT_TOKEN_SECRET", "git-token")
    monkeypatch.setenv("COMMIT_AUTHOR_NAME", "Bot")
    monkeypatch.setenv("COMMIT_AUTHOR_EMAIL", "bot@test.com")
    monkeypatch.setenv("EXCLUDE_PATHS", "")
    reset_config()


@pytest.fixture
def mock_drive():
    """Create a mock DriveClient."""
    drive = MagicMock()
    drive.is_in_folder.return_value = True
    drive.get_file_path.return_value = "Reports/file.docx"
    drive.matches_exclude_pattern.return_value = False
    drive.should_skip_file.return_value = None
    return drive


@pytest.fixture
def mock_state():
    """Create a mock StateManager."""
    return MagicMock()


def _make_file_data(
    name="file.docx",
    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    md5="abc123",
    trashed=False,
    modified_time="2025-01-01T00:00:00Z",
    parents=None,
    author_name="Alice",
    author_email="alice@example.com",
):
    """Helper to build a realistic file_data dict."""
    data = {
        "id": "fileId1",
        "name": name,
        "mimeType": mime_type,
        "trashed": trashed,
        "modifiedTime": modified_time,
        "parents": parents or ["folder123"],
        "lastModifyingUser": {
            "displayName": author_name,
            "emailAddress": author_email,
        },
    }
    if md5 is not None:
        data["md5Checksum"] = md5
    return data


# ---------------------------------------------------------------------------
# classify_change
# ---------------------------------------------------------------------------


class TestClassifyChange:
    """Tests for the change classification logic."""

    def test_new_file_classified_as_add(self, mock_drive, mock_state):
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = None  # not tracked yet
        file_data = _make_file_data()
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result is not None
        assert result.change_type == ChangeType.ADD
        assert result.new_path == "Reports/file.docx"
        assert result.author_name == "Alice"

    def test_modified_file_different_md5(self, mock_drive, mock_state):
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = {
            "name": "file.docx",
            "path": "Reports/file.docx",
            "md5": "old_md5",
        }
        file_data = _make_file_data(md5="new_md5")
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result.change_type == ChangeType.MODIFY

    def test_same_md5_classified_as_skip(self, mock_drive, mock_state):
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = {
            "name": "file.docx",
            "path": "Reports/file.docx",
            "md5": "abc123",
        }
        file_data = _make_file_data(md5="abc123")
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result.change_type == ChangeType.SKIP

    def test_renamed_file(self, mock_drive, mock_state):
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = {
            "name": "old_name.docx",
            "path": "Reports/old_name.docx",
            "md5": "abc123",
        }
        file_data = _make_file_data(name="new_name.docx", md5="abc123")
        mock_drive.get_file_path.return_value = "Reports/new_name.docx"
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result.change_type == ChangeType.RENAME
        assert result.old_path == "Reports/old_name.docx"
        assert result.new_path == "Reports/new_name.docx"

    def test_trashed_file_classified_as_delete(self, mock_drive, mock_state):
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = {
            "name": "file.docx",
            "path": "Reports/file.docx",
        }
        file_data = _make_file_data(trashed=True)
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result.change_type == ChangeType.DELETE
        assert result.old_path == "Reports/file.docx"

    def test_file_moved_out_of_folder_classified_as_delete(self, mock_drive, mock_state):
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = {
            "name": "file.docx",
            "path": "Reports/file.docx",
        }
        mock_drive.is_in_folder.return_value = False
        file_data = _make_file_data()
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result.change_type == ChangeType.DELETE
        assert result.old_path == "Reports/file.docx"

    def test_unknown_file_moved_out_returns_none(self, mock_drive, mock_state):
        from sync_engine import classify_change

        mock_state.get_file.return_value = None
        mock_drive.is_in_folder.return_value = False
        file_data = _make_file_data()
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result is None

    def test_google_native_file_different_modified_time(self, mock_drive, mock_state):
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = {
            "name": "My Doc",
            "path": "Reports/My Doc",
            "md5": None,
            "modified_time": "2025-01-01T00:00:00Z",
        }
        # Google Docs have no md5
        file_data = _make_file_data(
            name="My Doc",
            mime_type="application/vnd.google-apps.document",
            md5=None,
            modified_time="2025-06-15T12:00:00Z",
        )
        mock_drive.get_file_path.return_value = "Reports/My Doc"
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result.change_type == ChangeType.MODIFY

    def test_google_native_same_modified_time_skipped(self, mock_drive, mock_state):
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = {
            "name": "My Doc",
            "path": "Reports/My Doc",
            "md5": None,
            "modified_time": "2025-01-01T00:00:00Z",
        }
        file_data = _make_file_data(
            name="My Doc",
            mime_type="application/vnd.google-apps.document",
            md5=None,
            modified_time="2025-01-01T00:00:00Z",
        )
        mock_drive.get_file_path.return_value = "Reports/My Doc"
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result.change_type == ChangeType.SKIP

    def test_removed_flag_classified_as_delete(self, mock_drive, mock_state):
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = {
            "name": "file.docx",
            "path": "Reports/file.docx",
        }
        raw = {"removed": True, "file": {}}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result.change_type == ChangeType.DELETE

    def test_removed_unknown_file_returns_none(self, mock_drive, mock_state):
        from sync_engine import classify_change

        mock_state.get_file.return_value = None
        raw = {"removed": True, "file": {}}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result is None

    def test_excluded_file_returns_none(self, mock_drive, mock_state):
        from sync_engine import classify_change

        mock_state.get_file.return_value = None
        mock_drive.matches_exclude_pattern.return_value = True
        file_data = _make_file_data()
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result is None

    def test_skipped_file_returns_none(self, mock_drive, mock_state):
        from sync_engine import classify_change

        mock_state.get_file.return_value = None
        mock_drive.should_skip_file.return_value = "file too large"
        file_data = _make_file_data()
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result is None

    def test_file_moved_to_different_folder(self, mock_drive, mock_state):
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = {
            "name": "file.docx",
            "path": "Reports/file.docx",
            "md5": "abc123",
        }
        file_data = _make_file_data(md5="abc123")
        # Same name but different path indicates a move
        mock_drive.get_file_path.return_value = "Archive/file.docx"
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result.change_type == ChangeType.MOVE
        assert result.old_path == "Reports/file.docx"
        assert result.new_path == "Archive/file.docx"


# ---------------------------------------------------------------------------
# group_by_author
# ---------------------------------------------------------------------------


class TestGroupByAuthor:
    """Tests for grouping changes by author for separate commits."""

    def test_groups_by_author(self):
        from sync_engine import AuthorCommit, Change, ChangeType, group_by_author
        from config import get_config

        cfg = get_config()

        changes = [
            Change(
                file_id="f1",
                change_type=ChangeType.ADD,
                file_data={"name": "a.docx"},
                new_path="a.docx",
                author_name="Alice",
                author_email="alice@co.com",
            ),
            Change(
                file_id="f2",
                change_type=ChangeType.MODIFY,
                file_data={"name": "b.docx"},
                new_path="b.docx",
                author_name="Bob",
                author_email="bob@co.com",
            ),
            Change(
                file_id="f3",
                change_type=ChangeType.ADD,
                file_data={"name": "c.docx"},
                new_path="c.docx",
                author_name="Alice",
                author_email="alice@co.com",
            ),
        ]

        groups = group_by_author(changes, cfg)
        assert len(groups) == 2

        alice_group = [g for g in groups if g.author_name == "Alice"][0]
        assert len(alice_group.files) == 2
        assert "Sync from Google Drive" in alice_group.message
        assert "add: a.docx" in alice_group.message

    def test_fallback_to_config_author(self):
        from sync_engine import Change, ChangeType, group_by_author
        from config import get_config

        cfg = get_config()

        changes = [
            Change(
                file_id="f1",
                change_type=ChangeType.ADD,
                file_data={"name": "a.docx"},
                new_path="a.docx",
                author_name=None,
                author_email=None,
            ),
        ]

        groups = group_by_author(changes, cfg)
        assert len(groups) == 1
        assert groups[0].author_name == "Bot"
        assert groups[0].author_email == "bot@test.com"

    def test_message_includes_all_changes(self):
        from sync_engine import Change, ChangeType, group_by_author
        from config import get_config

        cfg = get_config()

        changes = [
            Change(
                file_id="f1",
                change_type=ChangeType.ADD,
                new_path="new.docx",
                author_name="Eve",
                author_email="eve@co.com",
            ),
            Change(
                file_id="f2",
                change_type=ChangeType.DELETE,
                old_path="old.docx",
                author_name="Eve",
                author_email="eve@co.com",
            ),
        ]

        groups = group_by_author(changes, cfg)
        msg = groups[0].message
        assert "add: new.docx" in msg
        assert "delete: old.docx" in msg

    def test_delete_uses_old_path_in_message(self):
        from sync_engine import Change, ChangeType, group_by_author
        from config import get_config

        cfg = get_config()

        changes = [
            Change(
                file_id="f1",
                change_type=ChangeType.DELETE,
                old_path="Archive/removed.pdf",
                author_name="Del",
                author_email="del@co.com",
            ),
        ]

        groups = group_by_author(changes, cfg)
        assert "Archive/removed.pdf" in groups[0].message


# ---------------------------------------------------------------------------
# update_file_state
# ---------------------------------------------------------------------------


class TestUpdateFileState:
    """Tests for Firestore state updates after commit."""

    def test_delete_removes_file(self, mock_state):
        from sync_engine import Change, ChangeType, update_file_state

        change = Change(
            file_id="f1",
            change_type=ChangeType.DELETE,
            old_path="Reports/deleted.docx",
        )
        update_file_state(change, mock_state)
        mock_state.delete_file.assert_called_once_with("f1")
        mock_state.set_file.assert_not_called()

    def test_add_sets_file_data(self, mock_state):
        from sync_engine import Change, ChangeType, update_file_state

        file_data = _make_file_data(
            name="report.docx",
            md5="abc123",
            modified_time="2025-06-15T12:00:00Z",
        )
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="Reports/report.docx",
        )
        update_file_state(change, mock_state)

        mock_state.set_file.assert_called_once()
        call_args = mock_state.set_file.call_args
        assert call_args[0][0] == "f1"
        stored = call_args[0][1]
        assert stored["name"] == "report.docx"
        assert stored["path"] == "Reports/report.docx"
        assert stored["md5"] == "abc123"
        assert stored["modified_time"] == "2025-06-15T12:00:00Z"
        assert stored["last_modified_by_name"] == "Alice"

    def test_modify_sets_file_data(self, mock_state):
        from sync_engine import Change, ChangeType, update_file_state

        file_data = _make_file_data(md5="new_md5")
        change = Change(
            file_id="f1",
            change_type=ChangeType.MODIFY,
            file_data=file_data,
            new_path="Reports/file.docx",
        )
        update_file_state(change, mock_state)

        mock_state.set_file.assert_called_once()
        stored = mock_state.set_file.call_args[0][1]
        assert stored["md5"] == "new_md5"

    def test_add_stores_extracted_path(self, mock_state):
        from sync_engine import Change, ChangeType, update_file_state

        file_data = _make_file_data(name="report.docx", md5="abc123")
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="Reports/report.docx",
        )
        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["extracted_path"] == "Reports/report.docx.md"

    def test_add_google_doc_stores_extracted_path(self, mock_state):
        from sync_engine import Change, ChangeType, update_file_state

        file_data = _make_file_data(
            name="My Doc",
            mime_type="application/vnd.google-apps.document",
            md5=None,
        )
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="My Doc",
        )
        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["extracted_path"] == "My Doc.docx.md"

    def test_csv_stores_txt_extracted_path(self, mock_state):
        from sync_engine import Change, ChangeType, update_file_state

        file_data = _make_file_data(
            name="data.csv",
            mime_type="text/csv",
            md5="csvmd5",
        )
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="data.csv",
        )
        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["extracted_path"] == "data.csv.txt"


# ---------------------------------------------------------------------------
# matches_exclude_pattern (via DriveClient mock)
# ---------------------------------------------------------------------------


class TestExcludePatterns:
    """Tests that classify_change correctly delegates to DriveClient exclude checks."""

    def test_excluded_path_not_classified(self, mock_drive, mock_state):
        from sync_engine import classify_change

        mock_state.get_file.return_value = None
        mock_drive.matches_exclude_pattern.return_value = True
        file_data = _make_file_data()
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result is None
        mock_drive.matches_exclude_pattern.assert_called_once()

    def test_non_excluded_path_classified(self, mock_drive, mock_state):
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = None
        mock_drive.matches_exclude_pattern.return_value = False
        file_data = _make_file_data()
        raw = {"file": file_data}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result is not None
        assert result.change_type == ChangeType.ADD


# ---------------------------------------------------------------------------
# Multi-author staging flow
# ---------------------------------------------------------------------------


class TestMultiAuthorCommit:
    """Tests that multi-author changes produce separate commits with correct staging."""

    def test_single_author_does_not_unstage(self):
        """When all changes are from one author, we skip unstage/restage."""
        from sync_engine import Change, ChangeType, group_by_author
        from config import get_config

        cfg = get_config()
        changes = [
            Change(file_id="f1", change_type=ChangeType.ADD, new_path="a.docx",
                   file_data={"name": "a.docx"}, author_name="Alice", author_email="alice@co.com"),
            Change(file_id="f2", change_type=ChangeType.ADD, new_path="b.docx",
                   file_data={"name": "b.docx"}, author_name="Alice", author_email="alice@co.com"),
        ]
        groups = group_by_author(changes, cfg)
        assert len(groups) == 1
        assert len(groups[0].files) == 2

    def test_multiple_authors_produce_separate_groups(self):
        """When changes come from multiple authors, each gets a separate group."""
        from sync_engine import Change, ChangeType, group_by_author
        from config import get_config

        cfg = get_config()
        changes = [
            Change(file_id="f1", change_type=ChangeType.ADD, new_path="a.docx",
                   file_data={"name": "a.docx"}, author_name="Alice", author_email="alice@co.com"),
            Change(file_id="f2", change_type=ChangeType.MODIFY, new_path="b.docx",
                   file_data={"name": "b.docx"}, author_name="Bob", author_email="bob@co.com"),
            Change(file_id="f3", change_type=ChangeType.ADD, new_path="c.docx",
                   file_data={"name": "c.docx"}, author_name="Alice", author_email="alice@co.com"),
        ]
        groups = group_by_author(changes, cfg)
        assert len(groups) == 2

        alice = [g for g in groups if g.author_name == "Alice"][0]
        bob = [g for g in groups if g.author_name == "Bob"][0]
        assert len(alice.files) == 2
        assert len(bob.files) == 1

    def test_stage_change_files_stages_original_and_extracted(self):
        """_stage_change_files stages both the original and extracted file."""
        from sync_engine import Change, ChangeType, _stage_change_files

        mock_repo = MagicMock()
        change = Change(
            file_id="f1", change_type=ChangeType.ADD, new_path="Reports/doc.docx",
            file_data={"name": "doc.docx", "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        )
        _stage_change_files(change, mock_repo, "docs")

        stage_calls = [call[0][0] for call in mock_repo.stage_file.call_args_list]
        assert "docs/Reports/doc.docx" in stage_calls
        assert "docs/Reports/doc.docx.md" in stage_calls

    def test_stage_change_files_delete_stages_old_path(self):
        """_stage_change_files for DELETE stages the old path."""
        from sync_engine import Change, ChangeType, _stage_change_files

        mock_repo = MagicMock()
        change = Change(file_id="f1", change_type=ChangeType.DELETE, old_path="Reports/old.docx")
        _stage_change_files(change, mock_repo, "docs")

        mock_repo.stage_file.assert_called_once_with("docs/Reports/old.docx")


# ---------------------------------------------------------------------------
# Resync loop (rapid changes regression)
# ---------------------------------------------------------------------------


class TestResyncLoop:
    """Regression tests for rapid save scenarios.

    When webhooks arrive while a sync is in progress, the resync flag
    ensures changes are picked up without waiting for the 4-hour safety-net.
    """

    def test_resync_flag_triggers_second_sync(self):
        """If resync_needed is set during sync, _run_sync_loop runs again."""
        from main import _run_sync_loop

        mock_state = MagicMock()
        # First call: resync needed. Second call: no resync needed.
        mock_state.is_resync_needed.side_effect = [True, False]
        mock_state.clear_resync_needed.return_value = None

        with patch("main.GitRepo") as MockRepo, \
             patch("main.DriveClient") as MockDrive, \
             patch("main.run_sync", return_value=1) as mock_sync:
            MockRepo.return_value.cleanup.return_value = None
            _run_sync_loop(mock_state)

            # run_sync called twice (initial + resync)
            assert mock_sync.call_count == 2

    def test_resync_loop_caps_at_max_iterations(self):
        """Continuous edits don't cause unbounded looping."""
        from main import _run_sync_loop

        mock_state = MagicMock()
        # Always says resync needed
        mock_state.is_resync_needed.return_value = True
        mock_state.clear_resync_needed.return_value = None

        with patch("main.GitRepo") as MockRepo, \
             patch("main.DriveClient"), \
             patch("main.run_sync", return_value=1) as mock_sync:
            MockRepo.return_value.cleanup.return_value = None
            _run_sync_loop(mock_state, max_iterations=3)

            # Capped at 3 even though resync was always True
            assert mock_sync.call_count == 3

    def test_no_resync_flag_means_single_run(self):
        """Normal case: no concurrent webhooks, sync runs once."""
        from main import _run_sync_loop

        mock_state = MagicMock()
        mock_state.is_resync_needed.return_value = False
        mock_state.clear_resync_needed.return_value = None

        with patch("main.GitRepo") as MockRepo, \
             patch("main.DriveClient"), \
             patch("main.run_sync", return_value=0) as mock_sync:
            MockRepo.return_value.cleanup.return_value = None
            _run_sync_loop(mock_state)

            assert mock_sync.call_count == 1

    def test_dedup_keeps_latest_change_per_file(self):
        """When the same file is modified multiple times, only latest state is synced."""
        from sync_engine import ChangeType, classify_change

        mock_drive = MagicMock()
        mock_drive.is_in_folder.return_value = True
        mock_drive.get_file_path.return_value = "doc.docx"
        mock_drive.matches_exclude_pattern.return_value = False
        mock_drive.should_skip_file.return_value = None

        mock_state = MagicMock()
        mock_state.get_file.return_value = {
            "name": "doc.docx", "path": "doc.docx", "md5": "old_md5"
        }

        # Simulate 3 rapid saves — changes.list returns all 3
        raw_changes = [
            {"fileId": "f1", "file": _make_file_data(md5="md5_v1")},
            {"fileId": "f1", "file": _make_file_data(md5="md5_v2")},
            {"fileId": "f1", "file": _make_file_data(md5="md5_v3")},
        ]

        # Dedup keeps last entry
        deduped = {}
        for change in raw_changes:
            deduped[change["fileId"]] = change
        assert len(deduped) == 1
        assert deduped["f1"]["file"]["md5Checksum"] == "md5_v3"

        # Classify the deduped change
        result = classify_change("f1", deduped["f1"], mock_drive, mock_state)
        assert result.change_type == ChangeType.MODIFY
