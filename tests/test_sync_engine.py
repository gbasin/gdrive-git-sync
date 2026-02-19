"""Tests for functions/sync_engine.py.

DriveClient, StateManager, and GitRepo are mocked throughout.
"""

from unittest.mock import MagicMock, call, patch

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
        from config import get_config
        from sync_engine import Change, ChangeType, group_by_author

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
        from config import get_config
        from sync_engine import Change, ChangeType, group_by_author

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
        from config import get_config
        from sync_engine import Change, ChangeType, group_by_author

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
        from config import get_config
        from sync_engine import Change, ChangeType, group_by_author

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
        from config import get_config
        from sync_engine import Change, ChangeType, group_by_author

        cfg = get_config()
        changes = [
            Change(
                file_id="f1",
                change_type=ChangeType.ADD,
                new_path="a.docx",
                file_data={"name": "a.docx"},
                author_name="Alice",
                author_email="alice@co.com",
            ),
            Change(
                file_id="f2",
                change_type=ChangeType.ADD,
                new_path="b.docx",
                file_data={"name": "b.docx"},
                author_name="Alice",
                author_email="alice@co.com",
            ),
        ]
        groups = group_by_author(changes, cfg)
        assert len(groups) == 1
        assert len(groups[0].files) == 2

    def test_multiple_authors_produce_separate_groups(self):
        """When changes come from multiple authors, each gets a separate group."""
        from config import get_config
        from sync_engine import Change, ChangeType, group_by_author

        cfg = get_config()
        changes = [
            Change(
                file_id="f1",
                change_type=ChangeType.ADD,
                new_path="a.docx",
                file_data={"name": "a.docx"},
                author_name="Alice",
                author_email="alice@co.com",
            ),
            Change(
                file_id="f2",
                change_type=ChangeType.MODIFY,
                new_path="b.docx",
                file_data={"name": "b.docx"},
                author_name="Bob",
                author_email="bob@co.com",
            ),
            Change(
                file_id="f3",
                change_type=ChangeType.ADD,
                new_path="c.docx",
                file_data={"name": "c.docx"},
                author_name="Alice",
                author_email="alice@co.com",
            ),
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
            file_id="f1",
            change_type=ChangeType.ADD,
            new_path="Reports/doc.docx",
            file_data={
                "name": "doc.docx",
                "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            },
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

        with (
            patch("main.GitRepo") as MockRepo,
            patch("main.DriveClient"),
            patch("main.run_sync", return_value=1) as mock_sync,
        ):
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

        with (
            patch("main.GitRepo") as MockRepo,
            patch("main.DriveClient"),
            patch("main.run_sync", return_value=1) as mock_sync,
        ):
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

        with (
            patch("main.GitRepo") as MockRepo,
            patch("main.DriveClient"),
            patch("main.run_sync", return_value=0) as mock_sync,
        ):
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
        mock_state.get_file.return_value = {"name": "doc.docx", "path": "doc.docx", "md5": "old_md5"}

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


# ---------------------------------------------------------------------------
# _handle_delete
# ---------------------------------------------------------------------------


class TestHandleDelete:
    """Tests for deleting files from the repo."""

    def test_deletes_original_file(self, mock_state):
        from sync_engine import Change, ChangeType, _handle_delete

        mock_state.get_file.return_value = {"path": "Reports/old.docx", "extracted_path": None}
        mock_repo = MagicMock()
        change = Change(file_id="f1", change_type=ChangeType.DELETE, old_path="Reports/old.docx")

        _handle_delete(change, mock_repo, mock_state, "docs")
        mock_repo.delete_file.assert_called_once_with("docs/Reports/old.docx")

    def test_deletes_extracted_file_too(self, mock_state):
        from sync_engine import Change, ChangeType, _handle_delete

        mock_state.get_file.return_value = {
            "path": "Reports/doc.docx",
            "extracted_path": "Reports/doc.docx.md",
        }
        mock_repo = MagicMock()
        change = Change(file_id="f1", change_type=ChangeType.DELETE, old_path="Reports/doc.docx")

        _handle_delete(change, mock_repo, mock_state, "docs")
        calls = mock_repo.delete_file.call_args_list
        assert call("docs/Reports/doc.docx") in calls
        assert call("docs/Reports/doc.docx.md") in calls

    def test_no_old_path_does_nothing(self, mock_state):
        from sync_engine import Change, ChangeType, _handle_delete

        mock_repo = MagicMock()
        change = Change(file_id="f1", change_type=ChangeType.DELETE, old_path=None)

        _handle_delete(change, mock_repo, mock_state, "docs")
        mock_repo.delete_file.assert_not_called()

    def test_no_extracted_path_only_deletes_original(self, mock_state):
        from sync_engine import Change, ChangeType, _handle_delete

        mock_state.get_file.return_value = {"path": "data.csv", "extracted_path": None}
        mock_repo = MagicMock()
        change = Change(file_id="f1", change_type=ChangeType.DELETE, old_path="data.csv")

        _handle_delete(change, mock_repo, mock_state, "docs")
        mock_repo.delete_file.assert_called_once_with("docs/data.csv")

    def test_delete_when_no_state_entry(self, mock_state):
        """Delete still removes the original even when state has no record."""
        from sync_engine import Change, ChangeType, _handle_delete

        mock_state.get_file.return_value = None
        mock_repo = MagicMock()
        change = Change(file_id="f1", change_type=ChangeType.DELETE, old_path="lost.docx")

        _handle_delete(change, mock_repo, mock_state, "docs")
        mock_repo.delete_file.assert_called_once_with("docs/lost.docx")


# ---------------------------------------------------------------------------
# _handle_rename
# ---------------------------------------------------------------------------


class TestHandleRename:
    """Tests for renaming/moving files in the repo."""

    def test_renames_original_file(self, mock_state):
        from sync_engine import Change, ChangeType, _handle_rename

        mock_state.get_file.return_value = {"path": "old.docx", "extracted_path": None, "md5": "abc123"}
        mock_drive = MagicMock()
        mock_repo = MagicMock()
        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=_make_file_data(name="new.docx", md5="abc123"),
            old_path="old.docx",
            new_path="new.docx",
        )

        _handle_rename(change, mock_drive, mock_repo, mock_state, "docs")
        mock_repo.rename_file.assert_called_once_with("docs/old.docx", "docs/new.docx")

    def test_renames_extracted_file_too(self, mock_state):
        from sync_engine import Change, ChangeType, _handle_rename

        mock_state.get_file.return_value = {
            "path": "old.docx",
            "extracted_path": "old.docx.md",
            "md5": "abc123",
        }
        mock_drive = MagicMock()
        mock_repo = MagicMock()
        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=_make_file_data(name="new.docx", md5="abc123"),
            old_path="old.docx",
            new_path="new.docx",
        )

        _handle_rename(change, mock_drive, mock_repo, mock_state, "docs")
        rename_calls = mock_repo.rename_file.call_args_list
        assert call("docs/old.docx", "docs/new.docx") in rename_calls
        assert call("docs/old.docx.md", "docs/new.docx.md") in rename_calls

    def test_move_to_subfolder_renames_extracted_path(self, mock_state):
        from sync_engine import Change, ChangeType, _handle_rename

        mock_state.get_file.return_value = {
            "path": "doc.docx",
            "extracted_path": "doc.docx.md",
            "md5": "abc123",
        }
        mock_drive = MagicMock()
        mock_repo = MagicMock()
        change = Change(
            file_id="f1",
            change_type=ChangeType.MOVE,
            file_data=_make_file_data(name="doc.docx", md5="abc123"),
            old_path="doc.docx",
            new_path="Archive/doc.docx",
        )

        _handle_rename(change, mock_drive, mock_repo, mock_state, "docs")
        rename_calls = mock_repo.rename_file.call_args_list
        assert call("docs/doc.docx", "docs/Archive/doc.docx") in rename_calls
        assert call("docs/doc.docx.md", "docs/Archive/doc.docx.md") in rename_calls


# ---------------------------------------------------------------------------
# _download_and_extract
# ---------------------------------------------------------------------------


class TestDownloadAndExtract:
    """Tests for downloading files and extracting text."""

    @staticmethod
    def _fake_extract_ok(input_path, output_path, mime_type=None):
        """Side effect for extract_text that creates the output file."""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("extracted content")
        return True

    @patch("sync_engine.extract_text")
    def test_binary_docx_downloads_and_extracts(self, mock_extract):
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_extract.side_effect = self._fake_extract_ok

        mock_drive = MagicMock()
        mock_drive.download_file.return_value = b"fake docx bytes"

        mock_repo = MagicMock()

        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=_make_file_data(name="report.docx", md5="abc123"),
            new_path="Reports/report.docx",
        )

        _download_and_extract(change, mock_drive, mock_repo, "docs")

        # Original file written to repo
        mock_repo.write_file.assert_any_call("docs/Reports/report.docx", b"fake docx bytes")
        # download_file called (not export_file)
        mock_drive.download_file.assert_called_once_with("f1")
        mock_drive.export_file.assert_not_called()
        # extract_text called and extracted file written to repo
        mock_extract.assert_called_once()
        # Two write_file calls: original + extracted
        assert mock_repo.write_file.call_count == 2

    @patch("sync_engine.extract_text")
    def test_google_doc_exports_then_extracts(self, mock_extract):
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_extract.side_effect = self._fake_extract_ok

        mock_drive = MagicMock()
        mock_drive.export_file.return_value = b"exported docx bytes"

        mock_repo = MagicMock()

        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=_make_file_data(
                name="My Doc",
                mime_type="application/vnd.google-apps.document",
                md5=None,
            ),
            new_path="My Doc",
        )

        _download_and_extract(change, mock_drive, mock_repo, "docs")

        # export_file called with docx MIME type
        mock_drive.export_file.assert_called_once_with(
            "f1",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        mock_drive.download_file.assert_not_called()
        # Original (exported) file written
        mock_repo.write_file.assert_any_call("docs/My Doc.docx", b"exported docx bytes")

    @patch("sync_engine.extract_text")
    def test_google_slides_exports_as_pdf(self, mock_extract):
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_extract.side_effect = self._fake_extract_ok

        mock_drive = MagicMock()
        mock_drive.export_file.return_value = b"pdf bytes"

        mock_repo = MagicMock()

        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=_make_file_data(
                name="Slides Deck",
                mime_type="application/vnd.google-apps.presentation",
                md5=None,
            ),
            new_path="Slides Deck",
        )

        _download_and_extract(change, mock_drive, mock_repo, "docs")

        mock_drive.export_file.assert_called_once_with("f1", "application/pdf")
        mock_repo.write_file.assert_any_call("docs/Slides Deck.pdf", b"pdf bytes")

    @patch("sync_engine.extract_text")
    def test_google_sheet_exports_as_csv(self, mock_extract):
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_extract.side_effect = self._fake_extract_ok

        mock_drive = MagicMock()
        mock_drive.export_file.return_value = b"csv data"

        mock_repo = MagicMock()

        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=_make_file_data(
                name="Budget",
                mime_type="application/vnd.google-apps.spreadsheet",
                md5=None,
            ),
            new_path="Budget",
        )

        _download_and_extract(change, mock_drive, mock_repo, "docs")

        mock_drive.export_file.assert_called_once_with("f1", "text/csv")
        mock_repo.write_file.assert_any_call("docs/Budget.csv", b"csv data")

    @patch("sync_engine.extract_text")
    def test_non_extractable_file_only_written(self, mock_extract):
        """A plain text file should be written but not extracted."""
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_drive = MagicMock()
        mock_drive.download_file.return_value = b"plain text"

        mock_repo = MagicMock()

        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=_make_file_data(
                name="notes.txt",
                mime_type="text/plain",
                md5="xyz",
            ),
            new_path="notes.txt",
        )

        _download_and_extract(change, mock_drive, mock_repo, "docs")

        mock_repo.write_file.assert_called_once_with("docs/notes.txt", b"plain text")
        mock_extract.assert_not_called()

    @patch("sync_engine.extract_text")
    def test_extraction_failure_does_not_crash(self, mock_extract):
        """If extraction fails, the original file should still be written."""
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_extract.return_value = False  # extraction failed

        mock_drive = MagicMock()
        mock_drive.download_file.return_value = b"corrupt pdf"

        mock_repo = MagicMock()

        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=_make_file_data(name="bad.pdf", mime_type="application/pdf", md5="xyz"),
            new_path="bad.pdf",
        )

        _download_and_extract(change, mock_drive, mock_repo, "docs")

        # Original still written, only one write (no extracted file)
        mock_repo.write_file.assert_called_once_with("docs/bad.pdf", b"corrupt pdf")

    @patch("sync_engine.extract_text")
    def test_file_in_subfolder(self, mock_extract):
        """Extracted file is placed next to original in subfolder."""
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_extract.side_effect = self._fake_extract_ok

        mock_drive = MagicMock()
        mock_drive.download_file.return_value = b"data"

        mock_repo = MagicMock()

        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=_make_file_data(name="report.docx", md5="abc"),
            new_path="2025/Q1/report.docx",
        )

        _download_and_extract(change, mock_drive, mock_repo, "docs")

        # Original in subfolder
        mock_repo.write_file.assert_any_call("docs/2025/Q1/report.docx", b"data")
        # extract_text called (extracted file would be 2025/Q1/report.docx.md)
        mock_extract.assert_called_once()
        # Extracted file also written to repo
        assert mock_repo.write_file.call_count == 2


# ---------------------------------------------------------------------------
# process_changes
# ---------------------------------------------------------------------------


class TestProcessChanges:
    """Tests for the process_changes orchestrator."""

    def test_processes_add_change(self, mock_state):
        from sync_engine import Change, ChangeType, process_changes

        mock_drive = MagicMock()
        mock_drive.download_file.return_value = b"content"
        mock_repo = MagicMock()
        cfg = MagicMock()
        cfg.docs_subdir = "docs"

        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=_make_file_data(name="notes.txt", mime_type="text/plain", md5="abc"),
            new_path="notes.txt",
        )

        with patch("sync_engine.extract_text") as mock_extract:
            mock_extract.return_value = False
            result = process_changes([change], mock_drive, mock_repo, mock_state, cfg)

        assert len(result) == 1
        assert result[0].file_id == "f1"

    def test_processes_delete_change(self, mock_state):
        from sync_engine import Change, ChangeType, process_changes

        mock_state.get_file.return_value = {"path": "old.txt", "extracted_path": None}
        mock_drive = MagicMock()
        mock_repo = MagicMock()
        cfg = MagicMock()
        cfg.docs_subdir = "docs"

        change = Change(
            file_id="f1",
            change_type=ChangeType.DELETE,
            old_path="old.txt",
        )

        result = process_changes([change], mock_drive, mock_repo, mock_state, cfg)
        assert len(result) == 1
        mock_repo.delete_file.assert_called()

    def test_skips_failed_changes(self, mock_state):
        """If processing one change fails, others still get processed."""
        from sync_engine import Change, ChangeType, process_changes

        mock_drive = MagicMock()
        mock_drive.download_file.side_effect = [Exception("network error"), b"good content"]
        mock_repo = MagicMock()
        cfg = MagicMock()
        cfg.docs_subdir = "docs"

        changes = [
            Change(
                file_id="f1",
                change_type=ChangeType.ADD,
                file_data=_make_file_data(name="bad.txt", mime_type="text/plain", md5="a"),
                new_path="bad.txt",
            ),
            Change(
                file_id="f2",
                change_type=ChangeType.ADD,
                file_data=_make_file_data(name="good.txt", mime_type="text/plain", md5="b"),
                new_path="good.txt",
            ),
        ]

        with patch("sync_engine.extract_text") as mock_extract:
            mock_extract.return_value = False
            result = process_changes(changes, mock_drive, mock_repo, mock_state, cfg)

        # Only the second change succeeded
        assert len(result) == 1
        assert result[0].file_id == "f2"

    def test_processes_rename_change(self, mock_state):
        from sync_engine import Change, ChangeType, process_changes

        mock_state.get_file.return_value = {
            "path": "old.docx",
            "extracted_path": "old.docx.md",
            "md5": "abc123",
        }
        mock_drive = MagicMock()
        mock_repo = MagicMock()
        cfg = MagicMock()
        cfg.docs_subdir = "docs"

        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=_make_file_data(name="new.docx", md5="abc123"),
            old_path="old.docx",
            new_path="new.docx",
        )

        result = process_changes([change], mock_drive, mock_repo, mock_state, cfg)
        assert len(result) == 1
        mock_repo.rename_file.assert_called()

    def test_empty_changes_returns_empty(self, mock_state):
        from sync_engine import process_changes

        mock_drive = MagicMock()
        mock_repo = MagicMock()
        cfg = MagicMock()
        cfg.docs_subdir = "docs"

        result = process_changes([], mock_drive, mock_repo, mock_state, cfg)
        assert result == []

    def test_modify_change_re_downloads(self, mock_state):
        from sync_engine import Change, ChangeType, process_changes

        mock_drive = MagicMock()
        mock_drive.download_file.return_value = b"updated content"
        mock_repo = MagicMock()
        cfg = MagicMock()
        cfg.docs_subdir = "docs"

        change = Change(
            file_id="f1",
            change_type=ChangeType.MODIFY,
            file_data=_make_file_data(name="report.docx", md5="new_md5"),
            new_path="report.docx",
        )

        def _fake_extract(input_path, output_path, mime_type=None):
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("extracted")
            return True

        with patch("sync_engine.extract_text") as mock_extract:
            mock_extract.side_effect = _fake_extract
            result = process_changes([change], mock_drive, mock_repo, mock_state, cfg)

        assert len(result) == 1
        mock_drive.download_file.assert_called_once_with("f1")


# ---------------------------------------------------------------------------
# _stage_change_files edge cases
# ---------------------------------------------------------------------------


class TestStageChangeFilesEdgeCases:
    """Additional edge cases for _stage_change_files."""

    def test_rename_stages_both_old_and_new(self):
        from sync_engine import Change, ChangeType, _stage_change_files

        mock_repo = MagicMock()
        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            old_path="Reports/old.docx",
            new_path="Reports/new.docx",
            file_data={"name": "new.docx", "mimeType": "application/octet-stream"},
        )
        _stage_change_files(change, mock_repo, "docs")

        stage_calls = [c[0][0] for c in mock_repo.stage_file.call_args_list]
        assert "docs/Reports/old.docx" in stage_calls
        assert "docs/Reports/new.docx" in stage_calls

    def test_move_stages_both_old_and_new(self):
        from sync_engine import Change, ChangeType, _stage_change_files

        mock_repo = MagicMock()
        change = Change(
            file_id="f1",
            change_type=ChangeType.MOVE,
            old_path="a/file.txt",
            new_path="b/file.txt",
            file_data={"name": "file.txt", "mimeType": "text/plain"},
        )
        _stage_change_files(change, mock_repo, "docs")

        stage_calls = [c[0][0] for c in mock_repo.stage_file.call_args_list]
        assert "docs/a/file.txt" in stage_calls
        assert "docs/b/file.txt" in stage_calls

    def test_add_google_doc_stages_exported_and_extracted(self):
        """Stages the exported .docx path and extracted .docx.md for Google Docs."""
        from sync_engine import Change, ChangeType, _stage_change_files

        mock_repo = MagicMock()
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            new_path="My Doc",
            file_data={
                "name": "My Doc",
                "mimeType": "application/vnd.google-apps.document",
            },
        )
        _stage_change_files(change, mock_repo, "docs")

        stage_calls = [c[0][0] for c in mock_repo.stage_file.call_args_list]
        assert "docs/My Doc.docx" in stage_calls
        assert "docs/My Doc.docx.md" in stage_calls

    def test_add_google_slides_stages_exported_pdf_and_txt(self):
        """Google Slides stages the exported .pdf and extracted .pdf.txt."""
        from sync_engine import Change, ChangeType, _stage_change_files

        mock_repo = MagicMock()
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            new_path="Deck",
            file_data={
                "name": "Deck",
                "mimeType": "application/vnd.google-apps.presentation",
            },
        )
        _stage_change_files(change, mock_repo, "docs")

        stage_calls = [c[0][0] for c in mock_repo.stage_file.call_args_list]
        assert "docs/Deck.pdf" in stage_calls
        assert "docs/Deck.pdf.txt" in stage_calls

    def test_add_google_sheet_stages_exported_csv_and_txt(self):
        """Google Sheets stages the exported .csv and extracted .csv.txt."""
        from sync_engine import Change, ChangeType, _stage_change_files

        mock_repo = MagicMock()
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            new_path="Budget",
            file_data={
                "name": "Budget",
                "mimeType": "application/vnd.google-apps.spreadsheet",
            },
        )
        _stage_change_files(change, mock_repo, "docs")

        stage_calls = [c[0][0] for c in mock_repo.stage_file.call_args_list]
        assert "docs/Budget.csv" in stage_calls
        assert "docs/Budget.csv.txt" in stage_calls

    def test_add_google_doc_in_subfolder_stages_correct_paths(self):
        """Google Doc in a subfolder stages exported and extracted with correct dir."""
        from sync_engine import Change, ChangeType, _stage_change_files

        mock_repo = MagicMock()
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            new_path="Reports/My Doc",
            file_data={
                "name": "My Doc",
                "mimeType": "application/vnd.google-apps.document",
            },
        )
        _stage_change_files(change, mock_repo, "docs")

        stage_calls = [c[0][0] for c in mock_repo.stage_file.call_args_list]
        assert "docs/Reports/My Doc.docx" in stage_calls
        assert "docs/Reports/My Doc.docx.md" in stage_calls

    def test_add_pdf_stages_original_and_txt(self):
        from sync_engine import Change, ChangeType, _stage_change_files

        mock_repo = MagicMock()
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            new_path="Reports/invoice.pdf",
            file_data={
                "name": "invoice.pdf",
                "mimeType": "application/pdf",
            },
        )
        _stage_change_files(change, mock_repo, "docs")

        stage_calls = [c[0][0] for c in mock_repo.stage_file.call_args_list]
        assert "docs/Reports/invoice.pdf" in stage_calls
        assert "docs/Reports/invoice.pdf.txt" in stage_calls


# ---------------------------------------------------------------------------
# run_sync (full orchestrator)
# ---------------------------------------------------------------------------


class TestRunSync:
    """Tests for the run_sync orchestrator (sync_engine lines 47-122).

    All three dependencies — DriveClient, StateManager, GitRepo — are mocked.
    extract_text is patched so _download_and_extract creates the output file.
    """

    @staticmethod
    def _fake_extract_ok(input_path, output_path, mime_type=None):
        """Side effect for extract_text that creates the output file."""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("extracted content")
        return True

    def _raw_change(self, file_id="fileId1", file_data=None):
        """Build a raw changes.list entry."""
        return {"fileId": file_id, "file": file_data or _make_file_data()}

    # -- 1. First run: no page token -------------------------------------------

    def test_first_run_no_page_token(self, mock_drive, mock_state):
        """state has no page token -> get initial token, set it, return 0."""
        from sync_engine import run_sync

        mock_state.get_page_token.return_value = None
        mock_drive.get_start_page_token.return_value = "initial_token_42"
        mock_repo = MagicMock()

        result = run_sync(mock_drive, mock_state, mock_repo)

        assert result == 0
        mock_drive.get_start_page_token.assert_called_once()
        mock_state.set_page_token.assert_called_once_with("initial_token_42")
        # Should NOT clone, push, or list changes
        mock_repo.clone.assert_not_called()
        mock_drive.list_changes.assert_not_called()

    # -- 2. No changes returns zero --------------------------------------------

    def test_no_changes_returns_zero(self, mock_drive, mock_state):
        """Page token exists, list_changes returns empty -> update token, return 0."""
        from sync_engine import run_sync

        mock_state.get_page_token.return_value = "token_1"
        mock_drive.list_changes.return_value = ([], "token_2")
        mock_repo = MagicMock()

        result = run_sync(mock_drive, mock_state, mock_repo)

        assert result == 0
        mock_state.set_page_token.assert_called_once_with("token_2")
        mock_repo.clone.assert_not_called()

    # -- 3. All changes skipped returns zero -----------------------------------

    def test_all_changes_skipped_returns_zero(self, mock_drive, mock_state):
        """Changes exist but all classify as SKIP -> update token, return 0."""
        from sync_engine import run_sync

        mock_state.get_page_token.return_value = "token_1"
        # Same md5 as existing -> SKIP
        file_data = _make_file_data(md5="same_md5")
        mock_drive.list_changes.return_value = (
            [self._raw_change(file_data=file_data)],
            "token_2",
        )
        mock_state.get_file.return_value = {
            "name": "file.docx",
            "path": "Reports/file.docx",
            "md5": "same_md5",
        }
        mock_repo = MagicMock()

        result = run_sync(mock_drive, mock_state, mock_repo)

        assert result == 0
        mock_state.set_page_token.assert_called_once_with("token_2")
        mock_repo.clone.assert_not_called()

    # -- 4. Single file add — full pipeline ------------------------------------

    @patch("sync_engine.extract_text")
    def test_single_file_add_full_pipeline(self, mock_extract, mock_drive, mock_state):
        """One new file: download, extract, commit, push, update state."""
        from sync_engine import run_sync

        mock_extract.side_effect = self._fake_extract_ok

        mock_state.get_page_token.return_value = "token_1"
        mock_state.get_file.return_value = None  # new file

        file_data = _make_file_data(name="report.docx", md5="abc123")
        mock_drive.list_changes.return_value = (
            [self._raw_change(file_id="f1", file_data=file_data)],
            "token_2",
        )
        mock_drive.download_file.return_value = b"docx bytes"

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        result = run_sync(mock_drive, mock_state, mock_repo)

        assert result == 1
        # Verify the full sequence
        mock_repo.clone.assert_called_once()
        mock_drive.download_file.assert_called_once_with("f1")
        mock_extract.assert_called_once()
        mock_repo.commit.assert_called_once()
        mock_repo.push.assert_called_once()
        # State updated after push
        mock_state.set_file.assert_called_once()
        stored = mock_state.set_file.call_args[0][1]
        assert stored["name"] == "report.docx"
        assert stored["md5"] == "abc123"
        mock_state.set_page_token.assert_called_with("token_2")

    # -- 5. Push failure does NOT update state ---------------------------------

    @patch("sync_engine.extract_text")
    def test_push_failure_does_not_update_state(self, mock_extract, mock_drive, mock_state):
        """If push raises, state should NOT be updated (page token and file state stay unchanged).

        This is a critical data-consistency test: a push failure means the git
        remote was not updated, so Firestore must not advance either.
        """
        from sync_engine import run_sync

        mock_extract.side_effect = self._fake_extract_ok

        mock_state.get_page_token.return_value = "token_1"
        mock_state.get_file.return_value = None  # new file

        file_data = _make_file_data(name="report.docx", md5="abc123")
        mock_drive.list_changes.return_value = (
            [self._raw_change(file_id="f1", file_data=file_data)],
            "token_2",
        )
        mock_drive.download_file.return_value = b"docx bytes"

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True
        mock_repo.push.side_effect = RuntimeError("push rejected")

        with pytest.raises(RuntimeError, match="push rejected"):
            run_sync(mock_drive, mock_state, mock_repo)

        # File state must NOT be updated
        mock_state.set_file.assert_not_called()
        # Page token must NOT be advanced
        mock_state.set_page_token.assert_not_called()

    # -- 6. Multiple authors get separate commits ------------------------------

    @patch("sync_engine.extract_text")
    def test_multiple_authors_get_separate_commits(self, mock_extract, mock_drive, mock_state):
        """Two changes from different authors produce two commits."""
        from sync_engine import run_sync

        mock_extract.side_effect = self._fake_extract_ok

        mock_state.get_page_token.return_value = "token_1"
        mock_state.get_file.return_value = None  # both are new files

        file_data_alice = _make_file_data(
            name="alice.docx", md5="aaa",
            author_name="Alice", author_email="alice@co.com",
        )
        file_data_bob = _make_file_data(
            name="bob.docx", md5="bbb",
            author_name="Bob", author_email="bob@co.com",
        )

        # Drive must return different paths for the two files
        mock_drive.get_file_path.side_effect = ["alice.docx", "bob.docx"]
        mock_drive.list_changes.return_value = (
            [
                self._raw_change(file_id="f_alice", file_data=file_data_alice),
                self._raw_change(file_id="f_bob", file_data=file_data_bob),
            ],
            "token_2",
        )
        mock_drive.download_file.return_value = b"content"

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        result = run_sync(mock_drive, mock_state, mock_repo)

        assert result == 2
        # Two separate commits — one per author
        assert mock_repo.commit.call_count == 2
        commit_authors = {c[0][1] for c in mock_repo.commit.call_args_list}
        assert "Alice" in commit_authors
        assert "Bob" in commit_authors
        # unstage_all called once before re-staging per author
        mock_repo.unstage_all.assert_called_once()
        mock_repo.push.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_rename with content change
# ---------------------------------------------------------------------------


class TestHandleRenameWithContentChange:
    """Tests for _handle_rename when the file is also modified (lines 300-309).

    After renaming the original and extracted files, _handle_rename checks
    whether the content also changed and, if so, re-downloads and re-extracts.
    """

    @staticmethod
    def _fake_extract_ok(input_path, output_path, mime_type=None):
        """Side effect for extract_text that creates the output file."""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("re-extracted content")
        return True

    @patch("sync_engine.extract_text")
    def test_rename_with_md5_change_re_downloads(self, mock_extract, mock_state):
        """File renamed AND md5 changed -> rename files AND re-download/extract."""
        from sync_engine import Change, ChangeType, _handle_rename

        mock_extract.side_effect = self._fake_extract_ok

        # Existing state with old md5
        mock_state.get_file.return_value = {
            "path": "old_report.docx",
            "extracted_path": "old_report.docx.md",
            "md5": "old_md5",
        }

        mock_drive = MagicMock()
        mock_drive.download_file.return_value = b"new docx content"

        mock_repo = MagicMock()

        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=_make_file_data(name="new_report.docx", md5="new_md5"),
            old_path="old_report.docx",
            new_path="new_report.docx",
        )

        _handle_rename(change, mock_drive, mock_repo, mock_state, "docs")

        # Original file was renamed
        rename_calls = mock_repo.rename_file.call_args_list
        assert call("docs/old_report.docx", "docs/new_report.docx") in rename_calls
        # Extracted file was renamed
        assert call("docs/old_report.docx.md", "docs/new_report.docx.md") in rename_calls

        # Content changed (md5 differs) so re-download happened
        mock_drive.download_file.assert_called_once_with("f1")
        # extract_text called for the re-extraction
        mock_extract.assert_called_once()
        # Re-extracted file written to repo
        assert mock_repo.write_file.call_count >= 1

    @patch("sync_engine.extract_text")
    def test_google_native_rename_always_re_extracts(self, mock_extract, mock_state):
        """Google Doc renamed (no md5) -> always re-download since modifiedTime likely changed."""
        from sync_engine import Change, ChangeType, _handle_rename

        mock_extract.side_effect = self._fake_extract_ok

        # Existing state for a Google Doc (no md5)
        mock_state.get_file.return_value = {
            "path": "Old Title",
            "extracted_path": "Old Title.docx.md",
            "md5": None,
        }

        mock_drive = MagicMock()
        mock_drive.export_file.return_value = b"exported docx bytes"

        mock_repo = MagicMock()

        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=_make_file_data(
                name="New Title",
                mime_type="application/vnd.google-apps.document",
                md5=None,
                modified_time="2025-06-15T12:00:00Z",
            ),
            old_path="Old Title",
            new_path="New Title",
        )

        _handle_rename(change, mock_drive, mock_repo, mock_state, "docs")

        # Files were renamed
        rename_calls = mock_repo.rename_file.call_args_list
        assert call("docs/Old Title", "docs/New Title") in rename_calls
        assert call("docs/Old Title.docx.md", "docs/New Title.docx.md") in rename_calls

        # Google-native: no md5, so always re-extracts
        mock_drive.export_file.assert_called_once_with(
            "f1",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        mock_extract.assert_called_once()
        # Re-exported file written to repo
        assert mock_repo.write_file.call_count >= 1
