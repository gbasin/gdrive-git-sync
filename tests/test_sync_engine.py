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
    state = MagicMock()
    state.get_file_by_target.return_value = None
    return state


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
        mock_state.get_file_by_target.return_value = None
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
        mock_state.get_file_by_target.return_value = None
        raw = {"removed": True, "file": {}}

        result = classify_change("fileId1", raw, mock_drive, mock_state)
        assert result is None

    def test_removed_target_file_deletes_tracked_shortcut(self, mock_drive, mock_state):
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = None
        mock_state.get_file_by_target.return_value = (
            "shortcut1",
            {"path": "Reports/link.docx", "name": "link.docx"},
        )
        raw = {"removed": True, "file": {}}

        result = classify_change("target1", raw, mock_drive, mock_state)
        assert result is not None
        assert result.change_type == ChangeType.DELETE
        assert result.file_id == "shortcut1"
        assert result.old_path == "Reports/link.docx"

    def test_trashed_target_file_deletes_tracked_shortcut(self, mock_drive, mock_state):
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = None
        mock_state.get_file_by_target.return_value = (
            "shortcut1",
            {"path": "Reports/link.docx", "name": "link.docx"},
        )
        raw = {"file": {"trashed": True}}

        result = classify_change("target1", raw, mock_drive, mock_state)
        assert result is not None
        assert result.change_type == ChangeType.DELETE
        assert result.file_id == "shortcut1"
        assert result.old_path == "Reports/link.docx"

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
            extracted_path_present=True,
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
            extracted_path_present=True,
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
            extracted_path_present=True,
        )
        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["extracted_path"] == "data.csv.txt"

    def test_modify_clears_extracted_path_when_extraction_failed(self, mock_state):
        from sync_engine import Change, ChangeType, update_file_state

        file_data = _make_file_data(md5="new_md5")
        change = Change(
            file_id="f1",
            change_type=ChangeType.MODIFY,
            file_data=file_data,
            new_path="Reports/file.docx",
            extracted_path_present=False,
        )
        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["extracted_path"] is None


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
        """_stage_change_files for DELETE stages the old path and extracted path."""
        from sync_engine import Change, ChangeType, _stage_change_files

        mock_repo = MagicMock()
        state = MagicMock()
        state.get_file.return_value = {
            "name": "old.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        change = Change(file_id="f1", change_type=ChangeType.DELETE, old_path="Reports/old.docx")
        _stage_change_files(change, mock_repo, "docs", state)

        stage_calls = [c[0][0] for c in mock_repo.stage_file.call_args_list]
        assert "docs/Reports/old.docx" in stage_calls
        assert "docs/Reports/old.docx.md" in stage_calls


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
        mock_state.get_watch_channel.return_value = None
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
        mock_state.get_watch_channel.return_value = None
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
        mock_state.get_watch_channel.return_value = None
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

        mock_state.get_file.return_value = {"name": "old.docx", "mime_type": "", "path": "Reports/old.docx"}
        mock_repo = MagicMock()
        change = Change(file_id="f1", change_type=ChangeType.DELETE, old_path="Reports/old.docx")

        _handle_delete(change, mock_repo, mock_state, "docs")
        calls = [c[0][0] for c in mock_repo.delete_file.call_args_list]
        assert "docs/Reports/old.docx" in calls

    def test_deletes_extracted_file_too(self, mock_state):
        from sync_engine import Change, ChangeType, _handle_delete

        mock_state.get_file.return_value = {
            "name": "doc.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "path": "Reports/doc.docx",
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

        mock_state.get_file.return_value = {"name": "notes.txt", "mime_type": "text/plain", "path": "notes.txt"}
        mock_repo = MagicMock()
        change = Change(file_id="f1", change_type=ChangeType.DELETE, old_path="notes.txt")

        _handle_delete(change, mock_repo, mock_state, "docs")
        mock_repo.delete_file.assert_called_once_with("docs/notes.txt")

    def test_delete_when_no_state_entry(self, mock_state):
        """Delete still removes the original even when state has no record."""
        from sync_engine import Change, ChangeType, _handle_delete

        mock_state.get_file.return_value = None
        mock_repo = MagicMock()
        change = Change(file_id="f1", change_type=ChangeType.DELETE, old_path="lost.docx")

        _handle_delete(change, mock_repo, mock_state, "docs")
        # No state means name comes from basename, mime_type is empty.
        # .docx extension means extracted .md is also deleted.
        calls = [c[0][0] for c in mock_repo.delete_file.call_args_list]
        assert "docs/lost.docx" in calls


# ---------------------------------------------------------------------------
# _handle_rename
# ---------------------------------------------------------------------------


class TestHandleRename:
    """Tests for renaming/moving files in the repo."""

    def test_renames_original_file(self, mock_state):
        from sync_engine import Change, ChangeType, _handle_rename

        mock_state.get_file.return_value = {"name": "old.docx", "path": "old.docx", "mime_type": "", "md5": "abc123"}
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

    def test_renames_extracted_file_too(self, mock_state):
        from sync_engine import Change, ChangeType, _handle_rename

        mock_state.get_file.return_value = {
            "name": "old.docx",
            "path": "old.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
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
            "name": "doc.docx",
            "path": "doc.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
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
            result, had_failures = process_changes([change], mock_drive, mock_repo, mock_state, cfg)

        assert len(result) == 1
        assert result[0].file_id == "f1"
        assert not had_failures

    def test_modify_docx_extract_failure_deletes_stale_sidecar(self, mock_state):
        from sync_engine import Change, ChangeType, process_changes

        mock_state.get_file.return_value = {
            "name": "file.docx",
            "path": "Reports/file.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "extracted_path": "Reports/file.docx.md",
        }
        mock_drive = MagicMock()
        mock_drive.download_file.return_value = b"updated docx bytes"
        mock_repo = MagicMock()
        cfg = MagicMock()
        cfg.docs_subdir = "docs"

        change = Change(
            file_id="f1",
            change_type=ChangeType.MODIFY,
            file_data=_make_file_data(name="file.docx", md5="new_md5"),
            new_path="Reports/file.docx",
        )

        with patch("sync_engine.extract_text") as mock_extract:
            mock_extract.return_value = False
            result, had_failures = process_changes([change], mock_drive, mock_repo, mock_state, cfg)

        assert len(result) == 1
        assert not had_failures
        assert change.extracted_path_present is False
        mock_repo.delete_file.assert_called_once_with("docs/Reports/file.docx.md")

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

        result, had_failures = process_changes([change], mock_drive, mock_repo, mock_state, cfg)
        assert len(result) == 1
        assert not had_failures
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
            result, had_failures = process_changes(changes, mock_drive, mock_repo, mock_state, cfg)

        # Only the second change succeeded
        assert len(result) == 1
        assert result[0].file_id == "f2"
        assert had_failures

    def test_processes_rename_change(self, mock_state):
        from sync_engine import Change, ChangeType, process_changes

        mock_state.get_file.return_value = {
            "name": "old.docx",
            "path": "old.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
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

        result, had_failures = process_changes([change], mock_drive, mock_repo, mock_state, cfg)
        assert len(result) == 1
        assert not had_failures
        mock_repo.rename_file.assert_called()

    def test_empty_changes_returns_empty(self, mock_state):
        from sync_engine import process_changes

        mock_drive = MagicMock()
        mock_repo = MagicMock()
        cfg = MagicMock()
        cfg.docs_subdir = "docs"

        result, had_failures = process_changes([], mock_drive, mock_repo, mock_state, cfg)
        assert result == []
        assert not had_failures

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
            result, had_failures = process_changes([change], mock_drive, mock_repo, mock_state, cfg)

        assert len(result) == 1
        assert not had_failures
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
        mock_repo.clone_or_init.assert_not_called()
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
        mock_repo.clone_or_init.assert_not_called()

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
        mock_repo.clone_or_init.assert_not_called()

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
        mock_repo.clone_or_init.assert_called_once()
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
            name="alice.docx",
            md5="aaa",
            author_name="Alice",
            author_email="alice@co.com",
        )
        file_data_bob = _make_file_data(
            name="bob.docx",
            md5="bbb",
            author_name="Bob",
            author_email="bob@co.com",
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
            "name": "old_report.docx",
            "path": "old_report.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
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
            "name": "Old Title",
            "path": "Old Title",
            "mime_type": "application/vnd.google-apps.document",
            "extracted_path": "Old Title.docx.md",
            "md5": None,
            "modified_time": "2025-01-01T00:00:00Z",
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

        # Files were renamed using git paths (with .docx extension)
        rename_calls = mock_repo.rename_file.call_args_list
        assert call("docs/Old Title.docx", "docs/New Title.docx") in rename_calls
        assert call("docs/Old Title.docx.md", "docs/New Title.docx.md") in rename_calls

        # Google-native: no md5, modifiedTime changed, so re-extracts
        mock_drive.export_file.assert_called_once_with(
            "f1",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        mock_extract.assert_called_once()
        # Re-exported file written to repo
        assert mock_repo.write_file.call_count >= 1


# ---------------------------------------------------------------------------
# Shortcut support
# ---------------------------------------------------------------------------

SHORTCUT_MIME = "application/vnd.google-apps.shortcut"


def _make_shortcut_data(
    shortcut_id="shortcut1",
    name="link.docx",
    target_id="target1",
    target_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    parents=None,
):
    """Helper to build a shortcut file_data dict."""
    return {
        "id": shortcut_id,
        "name": name,
        "mimeType": SHORTCUT_MIME,
        "trashed": False,
        "parents": parents or ["folder123"],
        "shortcutDetails": {
            "targetId": target_id,
            "targetMimeType": target_mime,
        },
        "lastModifyingUser": {
            "displayName": "Alice",
            "emailAddress": "alice@example.com",
        },
    }


class TestClassifyChangeShortcuts:
    """Tests for shortcut handling in classify_change."""

    def test_shortcut_resolved_and_classified_as_add(self, mock_drive, mock_state):
        """A new shortcut in our folder should resolve and be classified as ADD."""
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = None  # new file
        mock_drive.resolve_shortcut.return_value = {
            "id": "shortcut1",
            "name": "link.docx",
            "parents": ["folder123"],
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "md5Checksum": "target_md5",
            "modifiedTime": "2025-01-01T00:00:00Z",
            "_target_id": "target1",
            "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@example.com"},
        }
        raw = {"file": _make_shortcut_data()}

        result = classify_change("shortcut1", raw, mock_drive, mock_state)

        assert result is not None
        assert result.change_type == ChangeType.ADD
        assert result.file_id == "shortcut1"
        assert result.file_data["_target_id"] == "target1"
        assert result.file_data["mimeType"] != SHORTCUT_MIME

    def test_broken_shortcut_returns_none(self, mock_drive, mock_state):
        """A shortcut whose target is inaccessible should be skipped."""
        from sync_engine import classify_change

        mock_state.get_file.return_value = None
        mock_drive.resolve_shortcut.return_value = None
        raw = {"file": _make_shortcut_data()}

        result = classify_change("shortcut1", raw, mock_drive, mock_state)
        assert result is None

    def test_trashed_shortcut_skips_resolution(self, mock_drive, mock_state):
        """A trashed shortcut should NOT call resolve_shortcut (avoids wasted API call)."""
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = {"path": "link.docx", "name": "link.docx"}
        shortcut_data = _make_shortcut_data()
        shortcut_data["trashed"] = True
        raw = {"file": shortcut_data}

        result = classify_change("shortcut1", raw, mock_drive, mock_state)

        assert result is not None
        assert result.change_type == ChangeType.DELETE
        # resolve_shortcut should NOT have been called
        mock_drive.resolve_shortcut.assert_not_called()

    def test_removed_shortcut_skips_resolution(self, mock_drive, mock_state):
        """A removed shortcut should NOT call resolve_shortcut."""
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = {"path": "link.docx", "name": "link.docx"}
        raw = {"removed": True, "file": _make_shortcut_data()}

        result = classify_change("shortcut1", raw, mock_drive, mock_state)

        assert result is not None
        assert result.change_type == ChangeType.DELETE
        mock_drive.resolve_shortcut.assert_not_called()

    def test_target_file_change_triggers_shortcut_modify(self, mock_drive, mock_state):
        """When a shortcut target is modified, detect it via reverse-lookup."""
        from sync_engine import ChangeType, classify_change

        # Target file is not directly tracked
        mock_state.get_file.return_value = None
        mock_drive.is_in_folder.return_value = False
        mock_state.get_file_by_target.return_value = (
            "shortcut1",
            {"path": "Reports/link.docx", "name": "link.docx"},
        )

        target_file_data = _make_file_data(md5="new_md5")
        raw = {"file": target_file_data}

        result = classify_change("target1", raw, mock_drive, mock_state)

        assert result is not None
        assert result.change_type == ChangeType.MODIFY
        assert result.file_id == "shortcut1"
        assert result.file_data["id"] == "shortcut1"
        assert result.file_data["name"] == "link.docx"
        assert result.file_data["_target_id"] == "target1"
        assert result.new_path == "Reports/link.docx"

    def test_target_change_does_not_mutate_raw_dict(self, mock_drive, mock_state):
        """The target-change path should copy file_data, not mutate the raw dict."""
        from sync_engine import classify_change

        mock_state.get_file.return_value = None
        mock_drive.is_in_folder.return_value = False
        mock_state.get_file_by_target.return_value = (
            "shortcut1",
            {"path": "link.docx", "name": "link.docx"},
        )

        target_file_data = _make_file_data(md5="new_md5")
        raw = {"file": target_file_data}

        result = classify_change("target1", raw, mock_drive, mock_state)

        # The Change's file_data should have _target_id
        assert result.file_data["_target_id"] == "target1"
        # But the original raw dict should NOT be mutated
        assert "_target_id" not in target_file_data

    def test_target_change_no_reverse_match_returns_none(self, mock_drive, mock_state):
        """A change for an unknown file outside our folder returns None."""
        from sync_engine import classify_change

        mock_state.get_file.return_value = None
        mock_drive.is_in_folder.return_value = False
        mock_state.get_file_by_target.return_value = None
        raw = {"file": _make_file_data()}

        result = classify_change("unknown_file", raw, mock_drive, mock_state)
        assert result is None


class TestDownloadShortcut:
    """Tests for downloading shortcut targets via _target_id."""

    @patch("sync_engine.extract_text")
    def test_download_uses_target_id(self, mock_extract):
        """_download_and_extract should use _target_id for the download call."""
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_extract.return_value = False

        mock_drive = MagicMock()
        mock_drive.download_file.return_value = b"target content"
        mock_repo = MagicMock()

        file_data = _make_file_data(name="link.docx", md5="abc")
        file_data["_target_id"] = "target1"

        change = Change(
            file_id="shortcut1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="Reports/link.docx",
        )

        _download_and_extract(change, mock_drive, mock_repo, "docs")

        # Should download from target, not shortcut
        mock_drive.download_file.assert_called_once_with("target1")

    @patch("sync_engine.extract_text")
    def test_export_uses_target_id(self, mock_extract):
        """Google-native shortcut targets should export using _target_id."""
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_extract.side_effect = lambda inp, out, mime_type=None: False

        mock_drive = MagicMock()
        mock_drive.export_file.return_value = b"exported bytes"
        mock_repo = MagicMock()

        file_data = _make_file_data(
            name="My Doc",
            mime_type="application/vnd.google-apps.document",
            md5=None,
        )
        file_data["_target_id"] = "target1"

        change = Change(
            file_id="shortcut1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="My Doc",
        )

        _download_and_extract(change, mock_drive, mock_repo, "docs")

        # Should export from target, not shortcut
        mock_drive.export_file.assert_called_once_with(
            "target1",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    @patch("sync_engine.extract_text")
    def test_download_without_target_id_uses_file_id(self, mock_extract):
        """Regular (non-shortcut) files should use change.file_id for download."""
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_extract.return_value = False

        mock_drive = MagicMock()
        mock_drive.download_file.return_value = b"content"
        mock_repo = MagicMock()

        file_data = _make_file_data(name="regular.docx", md5="abc")
        # No _target_id

        change = Change(
            file_id="regular_file1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="regular.docx",
        )

        _download_and_extract(change, mock_drive, mock_repo, "docs")

        mock_drive.download_file.assert_called_once_with("regular_file1")


class TestUpdateFileStateShortcut:
    """Tests for target_id persistence in update_file_state."""

    def test_shortcut_stores_target_id(self, mock_state):
        """update_file_state should persist target_id when _target_id is present."""
        from sync_engine import Change, ChangeType, update_file_state

        file_data = _make_file_data(name="link.docx", md5="abc")
        file_data["_target_id"] = "target1"

        change = Change(
            file_id="shortcut1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="Reports/link.docx",
        )

        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["target_id"] == "target1"

    def test_regular_file_omits_target_id(self, mock_state):
        """update_file_state should NOT include target_id for regular files."""
        from sync_engine import Change, ChangeType, update_file_state

        file_data = _make_file_data(name="regular.docx", md5="abc")
        # No _target_id

        change = Change(
            file_id="regular1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="regular.docx",
        )

        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert "target_id" not in stored


# ---------------------------------------------------------------------------
# Folder change expansion
# ---------------------------------------------------------------------------


class TestFolderChangeExpansion:
    """Tests for _handle_folder_change and _cascade_folder_delete."""

    def test_folder_rename_expands_to_child_moves(self):
        from sync_engine import _handle_folder_change

        mock_drive = MagicMock()
        mock_drive.is_in_folder.return_value = True
        # Two children under the renamed folder
        mock_drive.list_folder_files.return_value = [
            {"id": "c1", "name": "a.docx", "parents": ["folder1"]},
            {"id": "c2", "name": "b.pdf", "parents": ["folder1"]},
        ]
        mock_drive.get_file_path.side_effect = ["NewFolder/a.docx", "NewFolder/b.pdf"]

        mock_state = MagicMock()
        mock_state.get_file.side_effect = [
            {"path": "OldFolder/a.docx", "name": "a.docx"},
            {"path": "OldFolder/b.pdf", "name": "b.pdf"},
        ]

        file_data = {
            "name": "NewFolder",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["folder123"],
            "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
        }

        result = _handle_folder_change("folder1", file_data, mock_drive, mock_state)
        assert len(result) == 2
        assert result[0].change_type == "move"
        assert result[0].old_path == "OldFolder/a.docx"
        assert result[0].new_path == "NewFolder/a.docx"
        assert result[1].old_path == "OldFolder/b.pdf"
        assert result[1].new_path == "NewFolder/b.pdf"

    def test_folder_rename_skips_untracked_children(self):
        from sync_engine import _handle_folder_change

        mock_drive = MagicMock()
        mock_drive.is_in_folder.return_value = True
        mock_drive.list_folder_files.return_value = [
            {"id": "c1", "name": "a.docx", "parents": ["folder1"]},
        ]

        mock_state = MagicMock()
        mock_state.get_file.return_value = None  # not tracked

        file_data = {
            "name": "Folder",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["folder123"],
            "lastModifyingUser": {},
        }

        result = _handle_folder_change("folder1", file_data, mock_drive, mock_state)
        assert result == []

    def test_folder_moved_out_cascades_deletes(self):
        from sync_engine import _cascade_folder_delete

        mock_drive = MagicMock()
        mock_drive.list_folder_files.return_value = [
            {"id": "c1"},
            {"id": "c2"},
        ]

        mock_state = MagicMock()
        mock_state.get_file.side_effect = [
            {"path": "Folder/a.docx"},
            {"path": "Folder/b.pdf"},
        ]

        file_data = {"name": "Folder", "parents": ["external"]}
        result = _cascade_folder_delete("folder1", file_data, mock_drive, mock_state)
        assert len(result) == 2
        assert all(c.change_type == "delete" for c in result)

    def test_folder_cascade_falls_back_to_state(self):
        """When Drive API returns no children, fall back to state name-based search."""
        from sync_engine import _cascade_folder_delete

        mock_drive = MagicMock()
        mock_drive.list_folder_files.return_value = []  # API failed

        mock_state = MagicMock()
        mock_state.get_all_files.return_value = {
            "c1": {"path": "Projects/OldFolder/a.docx"},
            "c2": {"path": "Projects/OldFolder/b.pdf"},
            "c3": {"path": "Projects/OtherFolder/c.txt"},  # should NOT match
        }

        file_data = {"name": "OldFolder", "parents": ["projects_folder"]}
        result = _cascade_folder_delete("folder1", file_data, mock_drive, mock_state)
        assert len(result) == 2
        assert all(c.change_type == "delete" for c in result)
        paths = {c.old_path for c in result}
        assert "Projects/OldFolder/a.docx" in paths
        assert "Projects/OldFolder/b.pdf" in paths

    def test_folder_cascade_fallback_matches_top_level_folder(self):
        """Fallback matches folder at the root of the path tree."""
        from sync_engine import _cascade_folder_delete

        mock_drive = MagicMock()
        mock_drive.list_folder_files.return_value = []

        mock_state = MagicMock()
        mock_state.get_all_files.return_value = {
            "c1": {"path": "MyFolder/doc.docx"},
            "c2": {"path": "Other/file.txt"},
        }

        file_data = {"name": "MyFolder", "parents": ["external"]}
        result = _cascade_folder_delete("folder1", file_data, mock_drive, mock_state)
        assert len(result) == 1
        assert result[0].old_path == "MyFolder/doc.docx"

    def test_folder_not_in_folder_cascades_delete(self):
        """_handle_folder_change delegates to _cascade_folder_delete when folder is outside."""
        from sync_engine import _handle_folder_change

        mock_drive = MagicMock()
        mock_drive.is_in_folder.return_value = False
        mock_drive.list_folder_files.return_value = [{"id": "c1"}]

        mock_state = MagicMock()
        mock_state.get_file.return_value = {"path": "Folder/a.docx"}

        file_data = {
            "name": "Folder",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["external"],
            "lastModifyingUser": {},
        }

        result = _handle_folder_change("folder1", file_data, mock_drive, mock_state)
        assert len(result) == 1
        assert result[0].change_type == "delete"

    def test_folder_cascade_fallback_skips_ambiguous_names(self):
        """If the same folder name appears in multiple subtrees, skip to avoid data loss."""
        from sync_engine import _cascade_folder_delete

        mock_drive = MagicMock()
        mock_drive.list_folder_files.return_value = []

        mock_state = MagicMock()
        mock_state.get_all_files.return_value = {
            "c1": {"path": "A/Shared/doc.docx"},
            "c2": {"path": "B/Shared/other.pdf"},
        }

        file_data = {"name": "Shared", "parents": ["external"]}
        result = _cascade_folder_delete("folder1", file_data, mock_drive, mock_state)
        # Should NOT delete because "Shared" matches two distinct prefixes
        assert len(result) == 0

    def test_folder_in_classify_change_returns_list(self, mock_drive, mock_state):
        """classify_change with folder mimeType returns a list of Changes."""
        from sync_engine import classify_change

        mock_drive.is_in_folder.return_value = True
        mock_drive.list_folder_files.return_value = []
        mock_state.get_file.return_value = None

        raw = {
            "file": {
                "name": "SomeFolder",
                "mimeType": "application/vnd.google-apps.folder",
                "parents": ["folder123"],
                "lastModifyingUser": {},
            },
        }

        result = classify_change("folder1", raw, mock_drive, mock_state)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Dedup logic
# ---------------------------------------------------------------------------


class TestDedupChanges:
    """Tests for the dedup logic in run_sync that prefers direct over synthetic changes."""

    def test_direct_change_wins_over_synthetic_move(self):
        """When both a direct change (with file_data) and synthetic MOVE exist,
        the direct change should win."""
        from sync_engine import Change, ChangeType

        direct = Change(
            file_id="f1",
            change_type=ChangeType.MODIFY,
            file_data={"name": "doc.docx", "mimeType": "text/plain"},
            new_path="doc.docx",
        )
        synthetic = Change(
            file_id="f1",
            change_type=ChangeType.MOVE,
            file_data=None,
            old_path="old/doc.docx",
            new_path="new/doc.docx",
        )

        # Simulate dedup logic from run_sync
        changes = [synthetic, direct]
        seen: dict[str, Change] = {}
        for c in changes:
            if c.file_id in seen:
                if c.file_data is not None:
                    seen[c.file_id] = c
            else:
                seen[c.file_id] = c
        deduped = list(seen.values())

        assert len(deduped) == 1
        assert deduped[0].change_type == ChangeType.MODIFY
        assert deduped[0].file_data is not None

    def test_synthetic_stays_when_no_direct(self):
        """A synthetic MOVE with no competing direct change is kept."""
        from sync_engine import Change, ChangeType

        synthetic = Change(
            file_id="f1",
            change_type=ChangeType.MOVE,
            file_data=None,
            old_path="old/doc.docx",
            new_path="new/doc.docx",
        )

        changes = [synthetic]
        seen: dict[str, Change] = {}
        for c in changes:
            if c.file_id in seen:
                if c.file_data is not None:
                    seen[c.file_id] = c
            else:
                seen[c.file_id] = c
        deduped = list(seen.values())

        assert len(deduped) == 1
        assert deduped[0].change_type == ChangeType.MOVE


# ---------------------------------------------------------------------------
# Page token on partial failure
# ---------------------------------------------------------------------------


class TestPageTokenPartialFailure:
    """Ensure page token is NOT advanced when some changes fail."""

    @staticmethod
    def _fake_extract_ok(input_path, output_path, mime_type=None):
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("extracted content")
        return True

    @patch("sync_engine.extract_text")
    def test_partial_failure_keeps_old_page_token(self, mock_extract, mock_drive, mock_state):
        from sync_engine import run_sync

        mock_extract.side_effect = self._fake_extract_ok

        mock_state.get_page_token.return_value = "token_old"
        mock_state.get_file.return_value = None  # both new

        good_file = _make_file_data(name="good.txt", mime_type="text/plain", md5="g1")
        bad_file = _make_file_data(name="bad.txt", mime_type="text/plain", md5="b1")

        mock_drive.list_changes.return_value = (
            [
                {"fileId": "f_bad", "file": bad_file},
                {"fileId": "f_good", "file": good_file},
            ],
            "token_new",
        )
        mock_drive.get_file_path.side_effect = ["bad.txt", "good.txt"]
        # First download fails, second succeeds
        mock_drive.download_file.side_effect = [Exception("network"), b"good content"]

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True
        mock_repo.rename_file.return_value = True

        result = run_sync(mock_drive, mock_state, mock_repo)

        assert result == 1  # only 1 succeeded
        # Page token should NOT be advanced to token_new
        set_page_token_calls = [c for c in mock_state.set_page_token.call_args_list]
        # The only set_page_token call should NOT be "token_new"
        for c in set_page_token_calls:
            assert c[0][0] != "token_new"


# ---------------------------------------------------------------------------
# Webhook mass-delete guard (run_sync)
# ---------------------------------------------------------------------------


class TestWebhookDeleteGuard:
    """Tests for the unconditional delete stripping in run_sync.

    All deletes from the changes API are dropped because the API
    falsely reports 'removed' for files in shared folders.
    """

    @staticmethod
    def _fake_extract_ok(input_path, output_path, mime_type=None):
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("extracted content")
        return True

    @patch("sync_engine.extract_text")
    def test_all_deletes_stripped_non_deletes_processed(self, mock_extract, mock_drive, mock_state):
        """ALL delete changes are stripped; non-delete changes are still processed."""
        from sync_engine import run_sync

        mock_extract.side_effect = self._fake_extract_ok

        mock_state.get_page_token.return_value = "token_1"

        # Build 2 delete changes (trashed files) + 1 add change
        raw_changes = []
        for i in range(2):
            fid = f"del_{i}"
            raw_changes.append({"fileId": fid, "file": _make_file_data(name=f"del{i}.docx", trashed=True)})

        # One new file (add)
        add_file = _make_file_data(name="new.txt", mime_type="text/plain", md5="add1")
        raw_changes.append({"fileId": "f_add", "file": add_file})

        mock_drive.list_changes.return_value = (raw_changes, "token_2")
        mock_drive.get_file_path.return_value = "some/path.txt"
        mock_drive.download_file.return_value = b"content"

        # Each deleted file is tracked in state
        def get_file_side_effect(file_id):
            if file_id.startswith("del_"):
                return {"name": f"{file_id}.docx", "path": f"Reports/{file_id}.docx"}
            return None  # f_add is new

        mock_state.get_file.side_effect = get_file_side_effect

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        result = run_sync(mock_drive, mock_state, mock_repo)

        # Only the add was processed (deletes were dropped)
        assert result == 1
        mock_repo.clone_or_init.assert_called_once()
        mock_repo.push.assert_called_once()

    def test_single_delete_also_stripped(self, mock_drive, mock_state):
        """Even a single delete is stripped from webhook sync."""
        from sync_engine import run_sync

        mock_state.get_page_token.return_value = "token_1"

        raw_changes = [{"fileId": "del_0", "file": _make_file_data(name="del0.docx", trashed=True)}]

        mock_drive.list_changes.return_value = (raw_changes, "token_2")

        def get_file_side_effect(file_id):
            return {"name": f"{file_id}.docx", "path": f"Reports/{file_id}.docx"}

        mock_state.get_file.side_effect = get_file_side_effect

        mock_repo = MagicMock()

        result = run_sync(mock_drive, mock_state, mock_repo)

        # Delete was stripped, nothing left to process
        assert result == 0
        mock_state.set_page_token.assert_called_with("token_2")
        mock_repo.clone_or_init.assert_not_called()

    @patch("sync_engine.extract_text")
    def test_all_deletes_returns_zero_advances_token(self, mock_extract, mock_drive, mock_state):
        """When all changes are deletes, returns 0 and advances the page token."""
        from sync_engine import run_sync

        mock_state.get_page_token.return_value = "token_1"

        # 3 deletes, no other changes
        raw_changes = []
        for i in range(3):
            fid = f"del_{i}"
            raw_changes.append({"fileId": fid, "file": _make_file_data(name=f"del{i}.docx", trashed=True)})

        mock_drive.list_changes.return_value = (raw_changes, "token_2")
        mock_drive.get_file_path.return_value = "Reports/file.docx"

        def get_file_side_effect(file_id):
            return {"name": f"{file_id}.docx", "path": f"Reports/{file_id}.docx"}

        mock_state.get_file.side_effect = get_file_side_effect

        mock_repo = MagicMock()

        result = run_sync(mock_drive, mock_state, mock_repo)

        assert result == 0
        # Page token is advanced even though no changes were processed
        mock_state.set_page_token.assert_called_with("token_2")
        # No clone/push since nothing was processed
        mock_repo.clone_or_init.assert_not_called()


# ---------------------------------------------------------------------------
# Diff sync deferred deletes (run_diff_sync)
# ---------------------------------------------------------------------------


class TestDiffSyncDeleteVerification:
    """Tests for per-file delete verification in run_diff_sync.

    Each file missing from the Drive listing is verified via
    verify_file_deleted() before being treated as a delete.
    """

    @staticmethod
    def _fake_extract_ok(input_path, output_path, mime_type=None):
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("extracted content")
        return True

    @patch("sync_engine.extract_text")
    def test_verified_deletes_processed(self, mock_extract, mock_drive, mock_state):
        """When verify_file_deleted returns True, deletes are processed."""
        from sync_engine import run_diff_sync

        mock_extract.side_effect = self._fake_extract_ok

        # 3 files tracked in Firestore state
        all_state = {f"f{i}": {"name": f"f{i}.docx", "path": f"f{i}.docx", "md5": f"md5_{i}"} for i in range(3)}
        mock_state.get_all_files.return_value = all_state

        # Drive listing returns only 1 file (2 are missing)
        drive_files = [
            {
                "id": "f0",
                "name": "f0.docx",
                "mimeType": "text/plain",
                "md5Checksum": "md5_0",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            }
        ]
        mock_drive.list_all_files.return_value = drive_files
        mock_drive.get_file_path.return_value = "f0.docx"
        mock_drive.matches_exclude_pattern.return_value = False
        mock_drive.should_skip_file.return_value = None
        mock_drive.get_start_page_token.return_value = "new_token"

        # Both missing files are truly deleted
        mock_drive.verify_file_deleted.return_value = True

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        # 2 deletes should have been processed
        assert result == 2
        assert mock_drive.verify_file_deleted.call_count == 2
        mock_repo.push.assert_called_once()

    @patch("sync_engine.extract_text")
    def test_unverified_deletes_skipped(self, mock_extract, mock_drive, mock_state):
        """When verify_file_deleted returns False, deletes are skipped."""
        from sync_engine import run_diff_sync

        mock_extract.side_effect = self._fake_extract_ok

        # 3 files tracked in Firestore state
        all_state = {f"f{i}": {"name": f"f{i}.docx", "path": f"f{i}.docx", "md5": f"md5_{i}"} for i in range(3)}
        mock_state.get_all_files.return_value = all_state

        # Drive listing returns only 1 file (2 are missing)
        drive_files = [
            {
                "id": "f0",
                "name": "f0.docx",
                "mimeType": "text/plain",
                "md5Checksum": "md5_0",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            }
        ]
        mock_drive.list_all_files.return_value = drive_files
        mock_drive.get_file_path.return_value = "f0.docx"
        mock_drive.matches_exclude_pattern.return_value = False
        mock_drive.should_skip_file.return_value = None
        mock_drive.get_start_page_token.return_value = "new_token"

        # Files still exist (incomplete listing)
        mock_drive.verify_file_deleted.return_value = False

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        # 0 changes: existing file matches state, deletes were skipped
        assert result == 0
        assert mock_drive.verify_file_deleted.call_count == 2
        mock_repo.push.assert_not_called()

    @patch("sync_engine.extract_text")
    def test_mixed_verified_and_unverified(self, mock_extract, mock_drive, mock_state):
        """When some files are truly deleted and some are not, only verified
        deletes are processed."""
        from sync_engine import run_diff_sync

        mock_extract.side_effect = self._fake_extract_ok

        # 4 files tracked in Firestore state
        all_state = {f"f{i}": {"name": f"f{i}.docx", "path": f"f{i}.docx", "md5": f"md5_{i}"} for i in range(4)}
        mock_state.get_all_files.return_value = all_state

        # Drive listing returns only 1 file (3 are missing)
        drive_files = [
            {
                "id": "f0",
                "name": "f0.docx",
                "mimeType": "text/plain",
                "md5Checksum": "md5_0",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            }
        ]
        mock_drive.list_all_files.return_value = drive_files
        mock_drive.get_file_path.return_value = "f0.docx"
        mock_drive.matches_exclude_pattern.return_value = False
        mock_drive.should_skip_file.return_value = None
        mock_drive.get_start_page_token.return_value = "new_token"

        # f1 is truly deleted, f2 and f3 still exist
        def verify_side_effect(file_id):
            return file_id == "f1"

        mock_drive.verify_file_deleted.side_effect = verify_side_effect

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        # Only 1 delete processed (f1)
        assert result == 1
        assert mock_drive.verify_file_deleted.call_count == 3
        mock_repo.push.assert_called_once()


# ---------------------------------------------------------------------------
# _resolve_docs_subdir edge cases (lines 67, 73)
# ---------------------------------------------------------------------------


class TestResolveDocsSubdir:
    """Tests for _resolve_docs_subdir: explicit config, folder name, and fallback."""

    def test_explicit_docs_subdir_left_alone(self):
        """If cfg.docs_subdir is already set, do not overwrite it."""
        from sync_engine import _resolve_docs_subdir

        mock_drive = MagicMock()
        cfg = MagicMock()
        cfg.docs_subdir = "explicit_dir"

        _resolve_docs_subdir(mock_drive, cfg)

        # Should NOT call get_folder_name since docs_subdir was already set
        mock_drive.get_folder_name.assert_not_called()
        assert cfg.docs_subdir == "explicit_dir"

    def test_no_docs_subdir_resolved_from_drive(self):
        """If cfg.docs_subdir is empty, resolve from Drive folder name."""
        from sync_engine import _resolve_docs_subdir

        mock_drive = MagicMock()
        mock_drive.get_folder_name.return_value = "SharedDocs"
        cfg = MagicMock()
        cfg.docs_subdir = ""
        cfg.drive_folder_id = "folder123"

        _resolve_docs_subdir(mock_drive, cfg)

        mock_drive.get_folder_name.assert_called_once_with("folder123")
        assert cfg.docs_subdir == "SharedDocs"

    def test_no_docs_subdir_and_no_folder_name(self):
        """If Drive folder name is None, docs_subdir stays empty (repo root)."""
        from sync_engine import _resolve_docs_subdir

        mock_drive = MagicMock()
        mock_drive.get_folder_name.return_value = None
        cfg = MagicMock()
        cfg.docs_subdir = ""
        cfg.drive_folder_id = "folder123"

        _resolve_docs_subdir(mock_drive, cfg)

        mock_drive.get_folder_name.assert_called_once()
        # docs_subdir should not be changed (remains empty)
        assert cfg.docs_subdir == ""


# ---------------------------------------------------------------------------
# run_diff_sync internal loop (lines 376-459)
# ---------------------------------------------------------------------------


class TestRunDiffSyncLoop:
    """Tests for the diff sync main loop: ADD, MODIFY, RENAME, MOVE detection."""

    @staticmethod
    def _fake_extract_ok(input_path, output_path, mime_type=None):
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("extracted content")
        return True

    def _setup_diff_sync(self, mock_drive, mock_state, drive_files, all_state):
        """Common setup for diff sync tests."""
        mock_drive.list_all_files.return_value = drive_files
        mock_drive.matches_exclude_pattern.return_value = False
        mock_drive.should_skip_file.return_value = None
        mock_state.get_all_files.return_value = all_state
        mock_drive.get_start_page_token.return_value = "new_token"
        mock_drive.verify_file_deleted.return_value = True

    @patch("sync_engine.extract_text")
    def test_diff_sync_detects_new_file_as_add(self, mock_extract, mock_drive, mock_state):
        """A file in Drive not in Firestore state is classified as ADD."""
        from sync_engine import run_diff_sync

        mock_extract.side_effect = self._fake_extract_ok

        drive_files = [
            {
                "id": "new1",
                "name": "brand_new.txt",
                "mimeType": "text/plain",
                "md5Checksum": "md5_new",
                "modifiedTime": "2025-06-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        all_state = {}  # empty state -> file is new

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.return_value = "brand_new.txt"
        mock_drive.download_file.return_value = b"new content"

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        assert result == 1
        mock_drive.download_file.assert_called_once_with("new1")
        mock_repo.push.assert_called_once()

    @patch("sync_engine.extract_text")
    def test_diff_sync_detects_modify_by_md5(self, mock_extract, mock_drive, mock_state):
        """A file with changed md5Checksum is classified as MODIFY."""
        from sync_engine import run_diff_sync

        mock_extract.return_value = False

        drive_files = [
            {
                "id": "f1",
                "name": "report.txt",
                "mimeType": "text/plain",
                "md5Checksum": "md5_new",
                "modifiedTime": "2025-06-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        all_state = {
            "f1": {
                "name": "report.txt",
                "path": "report.txt",
                "md5": "md5_old",
                "modified_time": "2025-01-01T00:00:00Z",
            },
        }

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.return_value = "report.txt"
        mock_drive.download_file.return_value = b"updated content"

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        assert result == 1
        mock_drive.download_file.assert_called_once_with("f1")

    @patch("sync_engine.extract_text")
    def test_diff_sync_detects_modify_by_modified_time(self, mock_extract, mock_drive, mock_state):
        """A Google-native file (no md5) with changed modifiedTime is MODIFY."""
        from sync_engine import run_diff_sync

        mock_extract.side_effect = self._fake_extract_ok

        drive_files = [
            {
                "id": "f1",
                "name": "My Doc",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2025-06-15T12:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        all_state = {
            "f1": {"name": "My Doc", "path": "My Doc", "md5": None, "modified_time": "2025-01-01T00:00:00Z"},
        }

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.return_value = "My Doc"
        mock_drive.export_file.return_value = b"exported docx bytes"

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        assert result == 1
        mock_drive.export_file.assert_called_once()

    @patch("sync_engine.extract_text")
    def test_diff_sync_skips_unchanged_file(self, mock_extract, mock_drive, mock_state):
        """A file with matching md5 and present in git should NOT be re-downloaded."""
        from sync_engine import run_diff_sync

        drive_files = [
            {
                "id": "f1",
                "name": "stable.txt",
                "mimeType": "text/plain",
                "md5Checksum": "same_md5",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        all_state = {
            "f1": {
                "name": "stable.txt",
                "path": "stable.txt",
                "md5": "same_md5",
                "modified_time": "2025-01-01T00:00:00Z",
            },
        }

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.return_value = "stable.txt"
        mock_drive.get_folder_name.return_value = "MyDrive"
        mock_drive.get_start_page_token.return_value = "new_token"

        mock_repo = MagicMock()
        # File exists in git — reconciliation should find it
        mock_repo.list_tracked_files.return_value = ["MyDrive/stable.txt"]

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        assert result == 0
        # Clone IS called now for git verification, but no download happens
        mock_repo.clone_or_init.assert_called_once()
        mock_drive.download_file.assert_not_called()

    @patch("sync_engine.extract_text")
    def test_diff_sync_detects_rename(self, mock_extract, mock_drive, mock_state):
        """A file whose name changed is classified as RENAME."""
        from sync_engine import run_diff_sync

        mock_extract.return_value = False

        drive_files = [
            {
                "id": "f1",
                "name": "new_name.txt",
                "mimeType": "text/plain",
                "md5Checksum": "same_md5",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        all_state = {
            "f1": {"name": "old_name.txt", "path": "old_name.txt", "md5": "same_md5"},
        }

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.return_value = "new_name.txt"

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True
        mock_repo.rename_file.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        assert result == 1
        mock_repo.rename_file.assert_called()

    @patch("sync_engine.extract_text")
    def test_diff_sync_detects_move(self, mock_extract, mock_drive, mock_state):
        """A file with same name but different path is classified as MOVE."""
        from sync_engine import run_diff_sync

        mock_extract.return_value = False

        drive_files = [
            {
                "id": "f1",
                "name": "doc.txt",
                "mimeType": "text/plain",
                "md5Checksum": "same_md5",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        all_state = {
            "f1": {"name": "doc.txt", "path": "OldFolder/doc.txt", "md5": "same_md5"},
        }

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.return_value = "NewFolder/doc.txt"

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True
        mock_repo.rename_file.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        assert result == 1
        mock_repo.rename_file.assert_called()

    @patch("sync_engine.extract_text")
    def test_diff_sync_detects_delete(self, mock_extract, mock_drive, mock_state):
        """A file in state but not in Drive listing is classified as DELETE."""
        from sync_engine import run_diff_sync

        mock_extract.return_value = False

        # Drive listing has one file; state has two
        drive_files = [
            {
                "id": "f1",
                "name": "keep.txt",
                "mimeType": "text/plain",
                "md5Checksum": "md5_keep",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        all_state = {
            "f1": {"name": "keep.txt", "path": "keep.txt", "md5": "md5_keep"},
            "f2": {"name": "gone.txt", "path": "gone.txt", "md5": "md5_gone"},
        }

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.return_value = "keep.txt"
        mock_drive.get_folder_name.return_value = "MyDrive"
        mock_state.get_file.return_value = {"name": "gone.txt", "path": "gone.txt", "mime_type": ""}

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True
        # keep.txt exists in git — reconciliation should not re-add it
        mock_repo.list_tracked_files.return_value = ["MyDrive/keep.txt"]

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        # 1 delete processed (keep.txt matches state so no change)
        assert result == 1
        mock_repo.delete_file.assert_called()

    def test_diff_sync_no_files_and_no_state(self, mock_drive, mock_state):
        """When Drive returns nothing and state is empty, returns 0."""
        from sync_engine import run_diff_sync

        mock_drive.list_all_files.return_value = []
        mock_state.get_all_files.return_value = {}

        mock_repo = MagicMock()

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        assert result == 0
        mock_repo.clone_or_init.assert_not_called()

    def test_diff_sync_empty_drive_but_has_state_skips(self, mock_drive, mock_state):
        """When Drive returns nothing but state has files, skip to avoid false deletes."""
        from sync_engine import run_diff_sync

        mock_drive.list_all_files.return_value = []
        mock_state.get_all_files.return_value = {"f1": {"name": "a.txt", "path": "a.txt"}}

        mock_repo = MagicMock()

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        assert result == 0
        mock_repo.clone_or_init.assert_not_called()

    @patch("sync_engine.extract_text")
    def test_diff_sync_filters_excluded_files(self, mock_extract, mock_drive, mock_state):
        """Files matching exclude patterns should be skipped."""
        from sync_engine import run_diff_sync

        drive_files = [
            {
                "id": "f1",
                "name": "excluded.txt",
                "mimeType": "text/plain",
                "md5Checksum": "md5_1",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {},
            },
        ]
        mock_drive.list_all_files.return_value = drive_files
        mock_drive.get_file_path.return_value = "excluded.txt"
        mock_drive.matches_exclude_pattern.return_value = True  # excluded
        mock_drive.should_skip_file.return_value = None
        mock_state.get_all_files.return_value = {}
        mock_drive.get_start_page_token.return_value = "new_token"

        mock_repo = MagicMock()

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        assert result == 0

    @patch("sync_engine.extract_text")
    def test_diff_sync_filters_skip_files(self, mock_extract, mock_drive, mock_state):
        """Files that should_skip_file says to skip are excluded."""
        from sync_engine import run_diff_sync

        drive_files = [
            {
                "id": "f1",
                "name": "huge.bin",
                "mimeType": "application/octet-stream",
                "md5Checksum": "md5_1",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {},
            },
        ]
        mock_drive.list_all_files.return_value = drive_files
        mock_drive.get_file_path.return_value = "huge.bin"
        mock_drive.matches_exclude_pattern.return_value = False
        mock_drive.should_skip_file.return_value = "file too large"
        mock_state.get_all_files.return_value = {}
        mock_drive.get_start_page_token.return_value = "new_token"

        mock_repo = MagicMock()

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        assert result == 0

    @patch("sync_engine.extract_text")
    def test_diff_sync_multi_author_separate_commits(self, mock_extract, mock_drive, mock_state):
        """Diff sync with multiple authors produces separate commits."""
        from sync_engine import run_diff_sync

        mock_extract.return_value = False

        drive_files = [
            {
                "id": "f1",
                "name": "a.txt",
                "mimeType": "text/plain",
                "md5Checksum": "md5_a",
                "modifiedTime": "2025-06-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
            {
                "id": "f2",
                "name": "b.txt",
                "mimeType": "text/plain",
                "md5Checksum": "md5_b",
                "modifiedTime": "2025-06-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Bob", "emailAddress": "bob@co.com"},
            },
        ]
        all_state = {}  # both new

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.side_effect = ["a.txt", "b.txt"]
        mock_drive.download_file.return_value = b"content"

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        assert result == 2
        # Two authors -> unstage_all + two separate commits
        assert mock_repo.commit.call_count == 2
        mock_repo.unstage_all.assert_called_once()

    @patch("sync_engine.extract_text")
    def test_diff_sync_page_token_exception_handled(self, mock_extract, mock_drive, mock_state):
        """If setting page token raises, diff sync still returns normally."""
        from sync_engine import run_diff_sync

        mock_extract.return_value = False

        drive_files = [
            {
                "id": "f1",
                "name": "new.txt",
                "mimeType": "text/plain",
                "md5Checksum": "md5_new",
                "modifiedTime": "2025-06-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]

        self._setup_diff_sync(mock_drive, mock_state, drive_files, {})
        mock_drive.get_file_path.return_value = "new.txt"
        mock_drive.download_file.return_value = b"content"
        mock_drive.get_start_page_token.side_effect = Exception("API error")

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        # Should not raise
        result = run_diff_sync(mock_drive, mock_state, mock_repo)
        assert result == 1

    @patch("sync_engine.extract_text")
    def test_diff_sync_had_failures_logged(self, mock_extract, mock_drive, mock_state):
        """When some changes fail, had_failures is logged but sync continues."""
        from sync_engine import run_diff_sync

        mock_extract.return_value = False

        drive_files = [
            {
                "id": "f1",
                "name": "bad.txt",
                "mimeType": "text/plain",
                "md5Checksum": "md5_new",
                "modifiedTime": "2025-06-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
            {
                "id": "f2",
                "name": "good.txt",
                "mimeType": "text/plain",
                "md5Checksum": "md5_good",
                "modifiedTime": "2025-06-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Bob", "emailAddress": "bob@co.com"},
            },
        ]

        self._setup_diff_sync(mock_drive, mock_state, drive_files, {})
        mock_drive.get_file_path.side_effect = ["bad.txt", "good.txt"]
        mock_drive.download_file.side_effect = [Exception("download failed"), b"good"]

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        # Only the good file was processed
        assert result == 1


# ---------------------------------------------------------------------------
# update_file_state RENAME/MOVE path (lines 1059-1083)
# ---------------------------------------------------------------------------


class TestUpdateFileStateRenameMoveAndAdd:
    """Tests for update_file_state RENAME/MOVE and ADD full-write logic."""

    def test_rename_updates_path_and_name(self, mock_state):
        """RENAME merges existing state with new path and name."""
        from sync_engine import Change, ChangeType, update_file_state

        mock_state.get_file.return_value = {
            "name": "old.docx",
            "path": "Reports/old.docx",
            "md5": "old_md5",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "modified_time": "2025-01-01T00:00:00Z",
            "extracted_path": "Reports/old.docx.md",
        }

        file_data = _make_file_data(
            name="new.docx",
            md5="new_md5",
            modified_time="2025-06-01T00:00:00Z",
        )
        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=file_data,
            old_path="Reports/old.docx",
            new_path="Reports/new.docx",
            extracted_path_present=True,
        )

        update_file_state(change, mock_state)

        mock_state.set_file.assert_called_once()
        stored = mock_state.set_file.call_args[0][1]
        assert stored["path"] == "Reports/new.docx"
        assert stored["name"] == "new.docx"
        assert stored["md5"] == "new_md5"
        assert stored["modified_time"] == "2025-06-01T00:00:00Z"
        assert stored["extracted_path"] == "Reports/new.docx.md"
        assert stored["mime_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def test_move_updates_path_preserves_name(self, mock_state):
        """MOVE updates path and recomputes extracted_path, preserves name."""
        from sync_engine import Change, ChangeType, update_file_state

        mock_state.get_file.return_value = {
            "name": "doc.docx",
            "path": "FolderA/doc.docx",
            "md5": "abc",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "extracted_path": "FolderA/doc.docx.md",
        }

        # MOVE keeps same name but changes path
        file_data = _make_file_data(name="doc.docx", md5="abc")
        change = Change(
            file_id="f1",
            change_type=ChangeType.MOVE,
            file_data=file_data,
            old_path="FolderA/doc.docx",
            new_path="FolderB/doc.docx",
        )

        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["path"] == "FolderB/doc.docx"
        assert stored["extracted_path"] == "FolderB/doc.docx.md"

    def test_move_preserves_missing_extracted_path(self, mock_state):
        """MOVE keeps extracted_path empty when no sidecar existed."""
        from sync_engine import Change, ChangeType, update_file_state

        mock_state.get_file.return_value = {
            "name": "doc.docx",
            "path": "FolderA/doc.docx",
            "md5": "abc",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "extracted_path": None,
        }

        change = Change(
            file_id="f1",
            change_type=ChangeType.MOVE,
            file_data=_make_file_data(name="doc.docx", md5="abc"),
            old_path="FolderA/doc.docx",
            new_path="FolderB/doc.docx",
        )

        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["extracted_path"] is None

    def test_rename_clears_extracted_path_when_reextract_failed(self, mock_state):
        """RENAME clears extracted_path when re-download produced no sidecar."""
        from sync_engine import Change, ChangeType, update_file_state

        mock_state.get_file.return_value = {
            "name": "old.docx",
            "path": "FolderA/old.docx",
            "md5": "old_md5",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "extracted_path": "FolderA/old.docx.md",
        }

        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=_make_file_data(name="new.docx", md5="new_md5"),
            old_path="FolderA/old.docx",
            new_path="FolderB/new.docx",
            extracted_path_present=False,
        )

        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["path"] == "FolderB/new.docx"
        assert stored["extracted_path"] is None

    def test_rename_no_existing_state(self, mock_state):
        """RENAME when state.get_file returns None starts from empty dict."""
        from sync_engine import Change, ChangeType, update_file_state

        mock_state.get_file.return_value = None

        file_data = _make_file_data(name="new.txt", mime_type="text/plain", md5="xyz")
        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=file_data,
            old_path="old.txt",
            new_path="new.txt",
        )

        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["path"] == "new.txt"
        assert stored["name"] == "new.txt"

    def test_rename_updates_author_info(self, mock_state):
        """RENAME stores lastModifyingUser from file_data."""
        from sync_engine import Change, ChangeType, update_file_state

        mock_state.get_file.return_value = {
            "name": "old.docx",
            "path": "old.docx",
            "last_modified_by_name": "OldAuthor",
        }

        file_data = _make_file_data(
            name="new.docx",
            md5="md5",
            author_name="NewAuthor",
            author_email="new@co.com",
        )
        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=file_data,
            old_path="old.docx",
            new_path="new.docx",
        )

        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["last_modified_by_name"] == "NewAuthor"
        assert stored["last_modified_by_email"] == "new@co.com"

    def test_rename_recomputes_extracted_path_google_doc(self, mock_state):
        """RENAME of a Google Doc recomputes extracted_path correctly."""
        from sync_engine import Change, ChangeType, update_file_state

        mock_state.get_file.return_value = {
            "name": "Old Title",
            "path": "Old Title",
            "mime_type": "application/vnd.google-apps.document",
            "extracted_path": "Old Title.docx.md",
        }

        file_data = _make_file_data(
            name="New Title",
            mime_type="application/vnd.google-apps.document",
            md5=None,
            modified_time="2025-06-01T00:00:00Z",
        )
        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=file_data,
            old_path="Old Title",
            new_path="New Title",
            extracted_path_present=True,
        )

        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["path"] == "New Title"
        assert stored["extracted_path"] == "New Title.docx.md"
        assert stored["mime_type"] == "application/vnd.google-apps.document"

    def test_add_with_subfolder_stores_extracted_path(self, mock_state):
        """ADD in a subfolder computes extracted_path with directory."""
        from sync_engine import Change, ChangeType, update_file_state

        file_data = _make_file_data(
            name="report.docx",
            md5="abc123",
            modified_time="2025-06-01T00:00:00Z",
        )
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="2025/Q1/report.docx",
            extracted_path_present=True,
        )

        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["path"] == "2025/Q1/report.docx"
        assert stored["extracted_path"] == "2025/Q1/report.docx.md"

    def test_modify_full_write_updates_all_fields(self, mock_state):
        """MODIFY uses the ADD/MODIFY full-write logic with all fields."""
        from sync_engine import Change, ChangeType, update_file_state

        file_data = _make_file_data(
            name="file.docx",
            md5="new_md5",
            modified_time="2025-06-15T12:00:00Z",
            author_name="Bob",
            author_email="bob@co.com",
        )
        change = Change(
            file_id="f1",
            change_type=ChangeType.MODIFY,
            file_data=file_data,
            new_path="Reports/file.docx",
            extracted_path_present=True,
        )

        update_file_state(change, mock_state)

        stored = mock_state.set_file.call_args[0][1]
        assert stored["name"] == "file.docx"
        assert stored["path"] == "Reports/file.docx"
        assert stored["md5"] == "new_md5"
        assert stored["modified_time"] == "2025-06-15T12:00:00Z"
        assert stored["extracted_path"] == "Reports/file.docx.md"
        assert stored["last_modified_by_name"] == "Bob"
        assert stored["last_modified_by_email"] == "bob@co.com"


# ---------------------------------------------------------------------------
# _handle_rename edge cases (lines 841, 848-853)
# ---------------------------------------------------------------------------


class TestHandleRenameEdgeCases:
    """Tests for _handle_rename edge cases: missing paths, no file_data, moved_ok=False."""

    @staticmethod
    def _fake_extract_ok(input_path, output_path, mime_type=None):
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("re-extracted content")
        return True

    def test_missing_old_path_returns_early(self, mock_state):
        """_handle_rename with no old_path does nothing."""
        from sync_engine import Change, ChangeType, _handle_rename

        mock_drive = MagicMock()
        mock_repo = MagicMock()
        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=_make_file_data(),
            old_path=None,
            new_path="new.docx",
        )

        _handle_rename(change, mock_drive, mock_repo, mock_state, "docs")

        mock_repo.rename_file.assert_not_called()
        mock_drive.download_file.assert_not_called()

    def test_missing_new_path_returns_early(self, mock_state):
        """_handle_rename with no new_path does nothing."""
        from sync_engine import Change, ChangeType, _handle_rename

        mock_drive = MagicMock()
        mock_repo = MagicMock()
        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=_make_file_data(),
            old_path="old.docx",
            new_path=None,
        )

        _handle_rename(change, mock_drive, mock_repo, mock_state, "docs")

        mock_repo.rename_file.assert_not_called()

    def test_no_file_data_uses_existing_state(self, mock_state):
        """When file_data is None, _handle_rename falls back to existing state."""
        from sync_engine import Change, ChangeType, _handle_rename

        mock_state.get_file.return_value = {
            "name": "doc.txt",
            "path": "old/doc.txt",
            "mime_type": "text/plain",
        }
        mock_drive = MagicMock()
        mock_repo = MagicMock()
        mock_repo.rename_file.return_value = True

        change = Change(
            file_id="f1",
            change_type=ChangeType.MOVE,
            file_data=None,  # synthetic move from folder expansion
            old_path="old/doc.txt",
            new_path="new/doc.txt",
        )

        _handle_rename(change, mock_drive, mock_repo, mock_state, "docs")

        # Should rename using existing state info
        mock_repo.rename_file.assert_called_once_with("docs/old/doc.txt", "docs/new/doc.txt")
        # No download since file_data is None
        mock_drive.download_file.assert_not_called()

    def test_no_file_data_no_existing_state_uses_basename(self, mock_state):
        """When file_data is None and no state, falls back to basename."""
        from sync_engine import Change, ChangeType, _handle_rename

        mock_state.get_file.return_value = None
        mock_drive = MagicMock()
        mock_repo = MagicMock()
        mock_repo.rename_file.return_value = True

        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=None,
            old_path="old/report.txt",
            new_path="new/renamed.txt",
        )

        _handle_rename(change, mock_drive, mock_repo, mock_state, "docs")

        mock_repo.rename_file.assert_called_once_with("docs/old/report.txt", "docs/new/renamed.txt")

    @patch("sync_engine.extract_text")
    def test_move_failed_re_downloads(self, mock_extract, mock_state):
        """When rename_file returns False (source missing), re-download to new path."""
        from sync_engine import Change, ChangeType, _handle_rename

        mock_extract.side_effect = self._fake_extract_ok

        mock_state.get_file.return_value = {
            "name": "doc.docx",
            "path": "old/doc.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "md5": "same_md5",
        }
        mock_drive = MagicMock()
        mock_drive.download_file.return_value = b"re-downloaded content"

        mock_repo = MagicMock()
        mock_repo.rename_file.return_value = False  # source file missing

        change = Change(
            file_id="f1",
            change_type=ChangeType.MOVE,
            file_data=_make_file_data(name="doc.docx", md5="same_md5"),
            old_path="old/doc.docx",
            new_path="new/doc.docx",
        )

        _handle_rename(change, mock_drive, mock_repo, mock_state, "docs")

        # Since move failed, re-download should happen
        mock_drive.download_file.assert_called_once_with("f1")
        mock_repo.write_file.assert_called()

    @patch("sync_engine.extract_text")
    def test_rename_same_md5_no_existing_state_re_downloads(self, mock_extract, mock_state):
        """When existing state is None and moved_ok is True, need_download stays False initially
        but without existing state, no md5 comparison happens so no re-download."""
        from sync_engine import Change, ChangeType, _handle_rename

        mock_extract.side_effect = self._fake_extract_ok

        mock_state.get_file.return_value = None  # no existing state
        mock_drive = MagicMock()
        mock_drive.download_file.return_value = b"content"

        mock_repo = MagicMock()
        mock_repo.rename_file.return_value = True  # move succeeded

        change = Change(
            file_id="f1",
            change_type=ChangeType.RENAME,
            file_data=_make_file_data(name="new.txt", mime_type="text/plain", md5="md5"),
            old_path="old.txt",
            new_path="new.txt",
        )

        _handle_rename(change, mock_drive, mock_repo, mock_state, "docs")

        # moved_ok=True, no existing state -> need_download=False, no re-download
        mock_drive.download_file.assert_not_called()


# ---------------------------------------------------------------------------
# classify_change shortcut resolution edge cases (lines 517-536, 598-599)
# ---------------------------------------------------------------------------


class TestClassifyChangeShortcutEdgeCases:
    """Tests for shortcut name fallback from path in classify_change."""

    def test_shortcut_target_with_name_in_state(self, mock_drive, mock_state):
        """When shortcut state has a name, it is used in the merged file_data."""
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = None
        mock_drive.is_in_folder.return_value = False
        mock_state.get_file_by_target.return_value = (
            "shortcut1",
            {"path": "Reports/link.docx", "name": "link.docx"},
        )

        target_file_data = _make_file_data(name="target_original.docx", md5="new_md5")
        raw = {"file": target_file_data}

        result = classify_change("target1", raw, mock_drive, mock_state)

        assert result is not None
        assert result.change_type == ChangeType.MODIFY
        assert result.file_data["name"] == "link.docx"

    def test_shortcut_target_no_name_uses_path_basename(self, mock_drive, mock_state):
        """When shortcut state has no name but has a path, basename is used."""
        from sync_engine import classify_change

        mock_state.get_file.return_value = None
        mock_drive.is_in_folder.return_value = False
        mock_state.get_file_by_target.return_value = (
            "shortcut1",
            {"path": "Reports/from_path.docx", "name": ""},
        )

        target_file_data = _make_file_data(name="target_original.docx", md5="new_md5")
        raw = {"file": target_file_data}

        result = classify_change("target1", raw, mock_drive, mock_state)

        assert result is not None
        assert result.file_data["name"] == "from_path.docx"

    def test_shortcut_target_no_name_no_path(self, mock_drive, mock_state):
        """When shortcut state has neither name nor path, name is not overridden."""
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = None
        mock_drive.is_in_folder.return_value = False
        mock_state.get_file_by_target.return_value = (
            "shortcut1",
            {"path": "", "name": ""},
        )

        target_file_data = _make_file_data(name="target_original.docx", md5="new_md5")
        raw = {"file": target_file_data}

        result = classify_change("target1", raw, mock_drive, mock_state)

        assert result is not None
        assert result.change_type == ChangeType.MODIFY
        # Empty shortcut_name means the name from target stays
        assert result.file_data["name"] == "target_original.docx"

    def test_shortcut_target_name_none_uses_path(self, mock_drive, mock_state):
        """When shortcut state has name=None, falls back to path basename."""
        from sync_engine import classify_change

        mock_state.get_file.return_value = None
        mock_drive.is_in_folder.return_value = False
        mock_state.get_file_by_target.return_value = (
            "shortcut1",
            {"path": "Docs/shortcut_link.docx"},  # no "name" key at all
        )

        target_file_data = _make_file_data(name="target.docx", md5="md5")
        raw = {"file": target_file_data}

        result = classify_change("target1", raw, mock_drive, mock_state)

        assert result is not None
        assert result.file_data["name"] == "shortcut_link.docx"
        assert result.file_data["_target_id"] == "target1"

    def test_classify_change_shortcut_resolved_to_skip(self, mock_drive, mock_state):
        """A resolved shortcut pointing to a file with same md5 is SKIP."""
        from sync_engine import ChangeType, classify_change

        mock_state.get_file.return_value = {
            "name": "link.docx",
            "path": "Reports/link.docx",
            "md5": "same_md5",
        }
        mock_drive.resolve_shortcut.return_value = {
            "id": "shortcut1",
            "name": "link.docx",
            "parents": ["folder123"],
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "md5Checksum": "same_md5",
            "modifiedTime": "2025-01-01T00:00:00Z",
            "_target_id": "target1",
            "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
        }
        mock_drive.get_file_path.return_value = "Reports/link.docx"

        raw = {"file": _make_shortcut_data()}

        result = classify_change("shortcut1", raw, mock_drive, mock_state)
        assert result.change_type == ChangeType.SKIP


# ---------------------------------------------------------------------------
# run_sync dedup: classify_change returning list (lines 279, 281, 289-290)
# ---------------------------------------------------------------------------


class TestRunSyncListClassifyAndDedup:
    """Tests for run_sync handling of classify_change returning a list and dedup logic."""

    @patch("sync_engine.extract_text")
    def test_classify_returning_list_extends_changes(self, mock_extract, mock_drive, mock_state):
        """When classify_change returns a list (folder expansion), all non-SKIP changes
        are included and the dedup logic prefers direct over synthetic."""
        from sync_engine import run_sync

        mock_extract.return_value = False

        mock_state.get_page_token.return_value = "token_1"

        # A folder change that expands to child moves
        folder_data = {
            "name": "Renamed",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["folder123"],
            "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
        }

        # Also a direct file change for one of the children (with file_data)
        child_file_data = _make_file_data(name="child.txt", mime_type="text/plain", md5="new_md5")

        mock_drive.list_changes.return_value = (
            [
                {"fileId": "folder1", "file": folder_data},
                {"fileId": "child1", "file": child_file_data},
            ],
            "token_2",
        )

        # classify_change: folder returns list of moves, child returns MODIFY
        def fake_classify(file_id, raw, drive, state):
            from sync_engine import Change, ChangeType

            if file_id == "folder1":
                return [
                    Change(
                        file_id="child1",
                        change_type=ChangeType.MOVE,
                        file_data=None,  # synthetic
                        old_path="Old/child.txt",
                        new_path="Renamed/child.txt",
                        author_name="Alice",
                        author_email="alice@co.com",
                    ),
                ]
            # child1: direct MODIFY
            return Change(
                file_id="child1",
                change_type=ChangeType.MODIFY,
                file_data=child_file_data,
                new_path="Renamed/child.txt",
                author_name="Alice",
                author_email="alice@co.com",
            )

        mock_drive.download_file.return_value = b"content"
        mock_drive.get_file_path.return_value = "Renamed/child.txt"

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        with patch("sync_engine.classify_change", side_effect=fake_classify):
            result = run_sync(mock_drive, mock_state, mock_repo)

        # Dedup: direct MODIFY (with file_data) wins over synthetic MOVE (no file_data)
        assert result == 1
        mock_repo.commit.assert_called_once()

    @patch("sync_engine.extract_text")
    def test_classify_returning_none_is_skipped(self, mock_extract, mock_drive, mock_state):
        """When classify_change returns None for some changes, they are dropped."""
        from sync_engine import run_sync

        mock_extract.return_value = False

        mock_state.get_page_token.return_value = "token_1"
        # First file is unknown and outside our folder -> None
        # Second file is new -> ADD
        mock_state.get_file.return_value = None
        mock_state.get_file_by_target.return_value = None

        unknown_file = _make_file_data(name="unknown.txt", mime_type="text/plain", md5="u1")
        good_file = _make_file_data(name="good.txt", mime_type="text/plain", md5="g1")

        mock_drive.list_changes.return_value = (
            [
                {"fileId": "f_unknown", "file": unknown_file},
                {"fileId": "f_good", "file": good_file},
            ],
            "token_2",
        )
        # First file is outside folder -> classify returns None
        mock_drive.is_in_folder.side_effect = [False, True]
        mock_drive.get_file_path.return_value = "good.txt"
        mock_drive.download_file.return_value = b"content"

        mock_repo = MagicMock()
        mock_repo.has_staged_changes.return_value = True

        result = run_sync(mock_drive, mock_state, mock_repo)

        # Only the good file was processed
        assert result == 1
        mock_state.set_page_token.assert_called_with("token_2")

    def test_dedup_synthetic_not_replaced_by_another_synthetic(self):
        """Two synthetic changes (both file_data=None) — first one wins."""
        from sync_engine import Change, ChangeType

        first = Change(file_id="f1", change_type=ChangeType.MOVE, file_data=None, old_path="a", new_path="b")
        second = Change(file_id="f1", change_type=ChangeType.DELETE, file_data=None, old_path="a")

        changes = [first, second]
        seen: dict[str, Change] = {}
        for c in changes:
            if c.file_id in seen:
                if c.file_data is not None:
                    seen[c.file_id] = c
            else:
                seen[c.file_id] = c
        deduped = list(seen.values())

        assert len(deduped) == 1
        # First synthetic wins (DELETE doesn't replace MOVE since both have file_data=None)
        assert deduped[0].change_type == ChangeType.MOVE


# ---------------------------------------------------------------------------
# _stage_change_files edge cases (line 1002, 1046-1047)
# ---------------------------------------------------------------------------


class TestStageChangeFilesAdditional:
    """Additional edge cases for _stage_change_files."""

    def test_delete_no_old_path_returns_early(self):
        """DELETE with no old_path does nothing."""
        from sync_engine import Change, ChangeType, _stage_change_files

        mock_repo = MagicMock()
        change = Change(file_id="f1", change_type=ChangeType.DELETE, old_path=None)

        _stage_change_files(change, mock_repo, "docs")

        mock_repo.stage_file.assert_not_called()

    def test_add_no_file_data_stages_new_path(self):
        """ADD with new_path but no file_data stages the raw path."""
        from sync_engine import Change, ChangeType, _stage_change_files

        mock_repo = MagicMock()
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            new_path="Reports/doc.txt",
            file_data=None,
        )

        _stage_change_files(change, mock_repo, "docs")

        mock_repo.stage_file.assert_called_once_with("docs/Reports/doc.txt")


# ---------------------------------------------------------------------------
# _download_and_extract: 403 fileNotDownloadable fallback (lines 924-939)
# ---------------------------------------------------------------------------


class TestDownloadAndExtractFallback:
    """Tests for the download fallback when Drive returns 403 fileNotDownloadable."""

    @staticmethod
    def _fake_extract_ok(input_path, output_path, mime_type=None):
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("extracted content")
        return True

    @staticmethod
    def _make_http_error(status_code, error_details=None):
        """Build a mock HttpError with the given status_code and error_details."""
        from unittest.mock import PropertyMock

        exc = MagicMock()
        exc.__class__ = type("HttpError", (Exception,), {})
        type(exc).status_code = PropertyMock(return_value=status_code)
        type(exc).error_details = PropertyMock(return_value=error_details or [])
        # Make it behave like an exception for isinstance checks
        return exc

    @patch("sync_engine.extract_text")
    @patch("sync_engine._is_not_downloadable")
    def test_fallback_to_export_on_403(self, mock_is_not_dl, mock_extract):
        """When download_file raises 403 fileNotDownloadable and actual mimeType
        is a Google-native type, re-download as export."""
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_extract.side_effect = self._fake_extract_ok
        mock_is_not_dl.return_value = True

        mock_drive = MagicMock()
        mock_drive.download_file.side_effect = Exception("403 fileNotDownloadable")
        mock_drive.get_file_mime.return_value = "application/vnd.google-apps.document"
        mock_drive.export_file.return_value = b"exported docx bytes"

        mock_repo = MagicMock()

        # File appears as binary in metadata but is actually a Google Doc
        file_data = _make_file_data(
            name="tricky_doc",
            mime_type="application/octet-stream",
            md5=None,
        )
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="tricky_doc",
        )

        _download_and_extract(change, mock_drive, mock_repo, "docs")

        # download_file was tried first
        mock_drive.download_file.assert_called_once_with("f1")
        # Then export_file was used as fallback
        mock_drive.export_file.assert_called_once_with(
            "f1",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        # The exported file was written with the .docx extension
        mock_repo.write_file.assert_any_call("docs/tricky_doc.docx", b"exported docx bytes")
        # file_data mimeType was updated
        assert file_data["mimeType"] == "application/vnd.google-apps.document"

    @patch("sync_engine.extract_text")
    @patch("sync_engine._is_not_downloadable")
    def test_fallback_reraises_when_actual_mime_not_native(self, mock_is_not_dl, mock_extract):
        """When download fails and actual mimeType is not a native export type, re-raise."""
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_is_not_dl.return_value = True

        mock_drive = MagicMock()
        mock_drive.download_file.side_effect = Exception("403 fileNotDownloadable")
        mock_drive.get_file_mime.return_value = "application/pdf"  # not a native type

        mock_repo = MagicMock()

        file_data = _make_file_data(
            name="file.bin",
            mime_type="application/octet-stream",
            md5=None,
        )
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="file.bin",
        )

        with pytest.raises(Exception, match="403"):
            _download_and_extract(change, mock_drive, mock_repo, "docs")

    @patch("sync_engine.extract_text")
    @patch("sync_engine._is_not_downloadable")
    def test_fallback_reraises_when_actual_mime_is_none(self, mock_is_not_dl, mock_extract):
        """When download fails and get_file_mime returns None, re-raise."""
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_is_not_dl.return_value = True

        mock_drive = MagicMock()
        mock_drive.download_file.side_effect = Exception("403 fileNotDownloadable")
        mock_drive.get_file_mime.return_value = None

        mock_repo = MagicMock()

        file_data = _make_file_data(name="file.bin", mime_type="application/octet-stream", md5=None)
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="file.bin",
        )

        with pytest.raises(Exception, match="403"):
            _download_and_extract(change, mock_drive, mock_repo, "docs")

    @patch("sync_engine.extract_text")
    @patch("sync_engine._is_not_downloadable")
    def test_non_403_error_reraises(self, mock_is_not_dl, mock_extract):
        """When download fails with a non-403 error, re-raise immediately."""
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_is_not_dl.return_value = False  # not a 403

        mock_drive = MagicMock()
        mock_drive.download_file.side_effect = RuntimeError("network error")

        mock_repo = MagicMock()

        file_data = _make_file_data(name="file.docx", md5="abc")
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="file.docx",
        )

        with pytest.raises(RuntimeError, match="network error"):
            _download_and_extract(change, mock_drive, mock_repo, "docs")

        mock_drive.get_file_mime.assert_not_called()

    @patch("sync_engine.extract_text")
    @patch("sync_engine._is_not_downloadable")
    def test_fallback_in_subfolder_computes_correct_path(self, mock_is_not_dl, mock_extract):
        """When fallback export happens for a file in a subfolder, path is computed correctly."""
        from sync_engine import Change, ChangeType, _download_and_extract

        mock_extract.side_effect = self._fake_extract_ok
        mock_is_not_dl.return_value = True

        mock_drive = MagicMock()
        mock_drive.download_file.side_effect = Exception("403 fileNotDownloadable")
        mock_drive.get_file_mime.return_value = "application/vnd.google-apps.spreadsheet"
        mock_drive.export_file.return_value = b"csv data"

        mock_repo = MagicMock()

        file_data = _make_file_data(
            name="Budget",
            mime_type="application/octet-stream",
            md5=None,
        )
        change = Change(
            file_id="f1",
            change_type=ChangeType.ADD,
            file_data=file_data,
            new_path="Finance/Budget",
        )

        _download_and_extract(change, mock_drive, mock_repo, "docs")

        mock_drive.export_file.assert_called_once_with("f1", "text/csv")
        mock_repo.write_file.assert_any_call("docs/Finance/Budget.csv", b"csv data")


# ---------------------------------------------------------------------------
# Git-vs-Firestore reconciliation — diff sync
# ---------------------------------------------------------------------------


class TestDiffSyncGitReconciliation:
    """Verify diff sync detects Firestore-tracked files missing from git."""

    @staticmethod
    def _fake_extract_ok(input_path, output_path, mime_type=None):
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("extracted content")
        return True

    def _setup_diff_sync(self, mock_drive, mock_state, drive_files, all_state):
        """Common setup for diff sync tests."""
        mock_drive.list_all_files.return_value = drive_files
        mock_drive.matches_exclude_pattern.return_value = False
        mock_drive.should_skip_file.return_value = None
        mock_state.get_all_files.return_value = all_state
        mock_drive.get_start_page_token.return_value = "new_token"
        mock_drive.verify_file_deleted.return_value = True
        mock_drive.get_folder_name.return_value = "MyDrive"

    @patch("sync_engine.extract_text")
    def test_unchanged_file_missing_from_git_is_redownloaded(self, mock_extract, mock_drive, mock_state):
        """A file unchanged in Firestore+Drive but absent from git must be re-added."""
        from sync_engine import run_diff_sync

        mock_extract.return_value = False

        drive_files = [
            {
                "id": "f1",
                "name": "report.txt",
                "mimeType": "text/plain",
                "md5Checksum": "same_md5",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        all_state = {
            "f1": {
                "name": "report.txt",
                "path": "report.txt",
                "md5": "same_md5",
                "modified_time": "2025-01-01T00:00:00Z",
            },
        }

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.return_value = "report.txt"
        mock_drive.download_file.return_value = b"report content"

        mock_repo = MagicMock()
        # Git has NO files — the file is missing
        mock_repo.list_tracked_files.return_value = []
        mock_repo.has_staged_changes.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        # Should clone and re-download the missing file
        mock_repo.clone_or_init.assert_called_once()
        mock_drive.download_file.assert_called_once_with("f1")
        assert result == 1

    @patch("sync_engine.extract_text")
    def test_unchanged_file_present_in_git_is_not_redownloaded(self, mock_extract, mock_drive, mock_state):
        """A file unchanged AND present in git should NOT be re-downloaded."""
        from sync_engine import run_diff_sync

        mock_extract.return_value = False

        drive_files = [
            {
                "id": "f1",
                "name": "stable.txt",
                "mimeType": "text/plain",
                "md5Checksum": "same_md5",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        all_state = {
            "f1": {
                "name": "stable.txt",
                "path": "stable.txt",
                "md5": "same_md5",
                "modified_time": "2025-01-01T00:00:00Z",
            },
        }

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.return_value = "stable.txt"

        mock_repo = MagicMock()
        # Git DOES have the file
        mock_repo.list_tracked_files.return_value = ["MyDrive/stable.txt"]
        mock_repo.has_staged_changes.return_value = False

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        # Should NOT re-download
        mock_drive.download_file.assert_not_called()
        assert result == 0

    @patch("sync_engine.extract_text")
    def test_unchanged_file_missing_from_git_with_docs_subdir(self, mock_extract, mock_drive, mock_state, monkeypatch):
        """Git reconciliation accounts for docs_subdir prefix in path matching."""
        from sync_engine import run_diff_sync

        monkeypatch.setenv("DOCS_SUBDIR", "Documents")
        reset_config()

        mock_extract.return_value = False

        drive_files = [
            {
                "id": "f1",
                "name": "memo.txt",
                "mimeType": "text/plain",
                "md5Checksum": "same_md5",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        all_state = {
            "f1": {
                "name": "memo.txt",
                "path": "memo.txt",
                "md5": "same_md5",
                "modified_time": "2025-01-01T00:00:00Z",
            },
        }

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.return_value = "memo.txt"
        mock_drive.download_file.return_value = b"memo content"

        mock_repo = MagicMock()
        # Git has OTHER files but not this one (path would be Documents/memo.txt)
        mock_repo.list_tracked_files.return_value = ["Documents/other.txt"]
        mock_repo.has_staged_changes.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        mock_repo.clone_or_init.assert_called_once()
        mock_drive.download_file.assert_called_once_with("f1")
        assert result == 1

    @patch("sync_engine.extract_text")
    def test_unchanged_google_native_file_missing_from_git(self, mock_extract, mock_drive, mock_state):
        """A Google-native file (exported as .docx) missing from git is re-added."""
        from sync_engine import run_diff_sync

        mock_extract.side_effect = self._fake_extract_ok

        drive_files = [
            {
                "id": "f1",
                "name": "My Doc",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        all_state = {
            "f1": {
                "name": "My Doc",
                "path": "My Doc",
                "md5": None,
                "modified_time": "2025-01-01T00:00:00Z",
                "extracted_path": "My Doc.docx.md",
            },
        }

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.return_value = "My Doc"
        mock_drive.export_file.return_value = b"exported docx bytes"

        mock_repo = MagicMock()
        # Git does NOT have "MyDrive/My Doc.docx"
        mock_repo.list_tracked_files.return_value = []
        mock_repo.has_staged_changes.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        mock_repo.clone_or_init.assert_called_once()
        mock_drive.export_file.assert_called_once()
        assert result == 1

    @patch("sync_engine.extract_text")
    def test_unchanged_google_native_file_missing_extracted_sidecar_is_redownloaded(
        self, mock_extract, mock_drive, mock_state
    ):
        """A missing extracted sidecar should trigger reconciliation re-download."""
        from sync_engine import run_diff_sync

        mock_extract.side_effect = self._fake_extract_ok

        drive_files = [
            {
                "id": "f1",
                "name": "My Doc",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        all_state = {
            "f1": {
                "name": "My Doc",
                "path": "My Doc",
                "md5": None,
                "modified_time": "2025-01-01T00:00:00Z",
                "extracted_path": "My Doc.docx.md",
            },
        }

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.return_value = "My Doc"
        mock_drive.export_file.return_value = b"exported docx bytes"

        mock_repo = MagicMock()
        # Git has the exported .docx but is missing the extracted .docx.md sidecar.
        mock_repo.list_tracked_files.return_value = ["MyDrive/My Doc.docx"]
        mock_repo.has_staged_changes.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        mock_repo.clone_or_init.assert_called_once()
        mock_drive.export_file.assert_called_once()
        assert result == 1

    @patch("sync_engine.extract_text")
    def test_unchanged_google_native_missing_sidecar_not_required_when_state_says_absent(
        self, mock_extract, mock_drive, mock_state
    ):
        """Files without an extracted_path in state should not be re-downloaded."""
        from sync_engine import run_diff_sync

        mock_extract.side_effect = self._fake_extract_ok

        drive_files = [
            {
                "id": "f1",
                "name": "My Doc",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        all_state = {
            "f1": {
                "name": "My Doc",
                "path": "My Doc",
                "md5": None,
                "modified_time": "2025-01-01T00:00:00Z",
                "extracted_path": None,
            },
        }

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.return_value = "My Doc"

        mock_repo = MagicMock()
        mock_repo.list_tracked_files.return_value = ["MyDrive/My Doc.docx"]
        mock_repo.has_staged_changes.return_value = False

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        mock_drive.export_file.assert_not_called()
        assert result == 0

    @patch("sync_engine.extract_text")
    def test_unchanged_file_missing_from_git_with_other_changes(self, mock_extract, mock_drive, mock_state):
        """Reconciliation still heals missing files even when other changes exist."""
        from sync_engine import run_diff_sync

        mock_extract.return_value = False

        # f1 is unchanged (same md5) but missing from git
        # f2 is a brand new file (not in Firestore)
        drive_files = [
            {
                "id": "f1",
                "name": "old_file.txt",
                "mimeType": "text/plain",
                "md5Checksum": "same_md5",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
            {
                "id": "f2",
                "name": "new_file.txt",
                "mimeType": "text/plain",
                "md5Checksum": "md5_new",
                "modifiedTime": "2025-06-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Bob", "emailAddress": "bob@co.com"},
            },
        ]
        all_state = {
            "f1": {
                "name": "old_file.txt",
                "path": "old_file.txt",
                "md5": "same_md5",
                "modified_time": "2025-01-01T00:00:00Z",
            },
            # f2 not in state -> classified as ADD
        }

        self._setup_diff_sync(mock_drive, mock_state, drive_files, all_state)
        mock_drive.get_file_path.side_effect = lambda f: f["name"]
        mock_drive.download_file.return_value = b"content"

        mock_repo = MagicMock()
        # Git has neither file
        mock_repo.list_tracked_files.return_value = []
        mock_repo.has_staged_changes.return_value = True

        result = run_diff_sync(mock_drive, mock_state, mock_repo)

        # Both files should be processed: f2 as normal ADD, f1 as reconciliation ADD
        assert result >= 2
        assert mock_drive.download_file.call_count == 2


# ---------------------------------------------------------------------------
# Git-vs-Firestore reconciliation — initial sync
# ---------------------------------------------------------------------------


class TestInitialSyncGitReconciliation:
    """Verify initial sync detects idempotency-skipped files missing from git."""

    @staticmethod
    def _fake_extract_ok(input_path, output_path, mime_type=None):
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("extracted content")
        return True

    @patch("sync_engine.extract_text")
    def test_idempotency_skipped_file_missing_from_git_is_redownloaded(self, mock_extract, mock_drive, mock_state):
        """A file skipped by idempotency but absent from git must be re-added."""
        from sync_engine import run_initial_sync

        mock_extract.return_value = False
        mock_drive.get_folder_name.return_value = "MyDrive"

        drive_files = [
            {
                "id": "f1",
                "name": "report.txt",
                "mimeType": "text/plain",
                "md5Checksum": "same_md5",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        mock_drive.list_all_files.return_value = drive_files
        mock_drive.matches_exclude_pattern.return_value = False
        mock_drive.should_skip_file.return_value = None
        mock_drive.get_file_path.return_value = "report.txt"
        mock_drive.download_file.return_value = b"report content"

        # Firestore says file is already tracked with same md5 → idempotency skip
        mock_state.get_file.return_value = {
            "name": "report.txt",
            "path": "report.txt",
            "md5": "same_md5",
            "modified_time": "2025-01-01T00:00:00Z",
        }

        mock_repo = MagicMock()
        # But git does NOT have the file
        mock_repo.list_tracked_files.return_value = []
        mock_repo.has_staged_changes.return_value = True

        result = run_initial_sync(mock_drive, mock_state, mock_repo)

        # File should be re-downloaded even though Firestore says it's tracked
        mock_drive.download_file.assert_called_once_with("f1")
        assert result["count"] == 1

    @patch("sync_engine.extract_text")
    def test_idempotency_skipped_file_present_in_git_not_redownloaded(self, mock_extract, mock_drive, mock_state):
        """A file skipped by idempotency AND present in git should NOT be re-downloaded."""
        from sync_engine import run_initial_sync

        mock_extract.return_value = False
        mock_drive.get_folder_name.return_value = "MyDrive"

        drive_files = [
            {
                "id": "f1",
                "name": "stable.txt",
                "mimeType": "text/plain",
                "md5Checksum": "same_md5",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        mock_drive.list_all_files.return_value = drive_files
        mock_drive.matches_exclude_pattern.return_value = False
        mock_drive.should_skip_file.return_value = None
        mock_drive.get_file_path.return_value = "stable.txt"

        # Firestore says tracked with same md5
        mock_state.get_file.return_value = {
            "name": "stable.txt",
            "path": "stable.txt",
            "md5": "same_md5",
            "modified_time": "2025-01-01T00:00:00Z",
        }

        mock_repo = MagicMock()
        # Git DOES have the file
        mock_repo.list_tracked_files.return_value = ["MyDrive/stable.txt"]
        mock_repo.has_staged_changes.return_value = False

        result = run_initial_sync(mock_drive, mock_state, mock_repo)

        # Should NOT re-download
        mock_drive.download_file.assert_not_called()
        assert result["count"] == 0

    @patch("sync_engine.extract_text")
    def test_idempotency_skipped_google_native_missing_extracted_sidecar_is_redownloaded(
        self, mock_extract, mock_drive, mock_state
    ):
        """Initial sync should heal missing extracted sidecars for unchanged Google Docs."""
        from sync_engine import run_initial_sync

        mock_extract.side_effect = self._fake_extract_ok
        mock_drive.get_folder_name.return_value = "MyDrive"

        drive_files = [
            {
                "id": "f1",
                "name": "My Doc",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        mock_drive.list_all_files.return_value = drive_files
        mock_drive.matches_exclude_pattern.return_value = False
        mock_drive.should_skip_file.return_value = None
        mock_drive.get_file_path.return_value = "My Doc"
        mock_drive.export_file.return_value = b"exported docx bytes"

        mock_state.get_file.return_value = {
            "name": "My Doc",
            "path": "My Doc",
            "md5": None,
            "modified_time": "2025-01-01T00:00:00Z",
            "extracted_path": "My Doc.docx.md",
        }

        mock_repo = MagicMock()
        # Git has the exported .docx but is missing the extracted .docx.md sidecar.
        mock_repo.list_tracked_files.return_value = ["MyDrive/My Doc.docx"]
        mock_repo.has_staged_changes.return_value = True

        result = run_initial_sync(mock_drive, mock_state, mock_repo)

        mock_drive.export_file.assert_called_once_with(
            "f1", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert result["count"] == 1

    @patch("sync_engine.extract_text")
    def test_idempotency_skipped_google_native_missing_sidecar_not_required_when_state_says_absent(
        self, mock_extract, mock_drive, mock_state
    ):
        """Initial sync should not loop on files whose sidecar was never created."""
        from sync_engine import run_initial_sync

        mock_extract.side_effect = self._fake_extract_ok
        mock_drive.get_folder_name.return_value = "MyDrive"

        drive_files = [
            {
                "id": "f1",
                "name": "My Doc",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "lastModifyingUser": {"displayName": "Alice", "emailAddress": "alice@co.com"},
            },
        ]
        mock_drive.list_all_files.return_value = drive_files
        mock_drive.matches_exclude_pattern.return_value = False
        mock_drive.should_skip_file.return_value = None
        mock_drive.get_file_path.return_value = "My Doc"

        mock_state.get_file.return_value = {
            "name": "My Doc",
            "path": "My Doc",
            "md5": None,
            "modified_time": "2025-01-01T00:00:00Z",
            "extracted_path": None,
        }

        mock_repo = MagicMock()
        mock_repo.list_tracked_files.return_value = ["MyDrive/My Doc.docx"]
        mock_repo.has_staged_changes.return_value = False

        result = run_initial_sync(mock_drive, mock_state, mock_repo)

        mock_drive.export_file.assert_not_called()
        assert result["count"] == 0
