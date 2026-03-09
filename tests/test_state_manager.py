"""Tests for functions/state_manager.py.

All Firestore interactions are mocked so no real database is needed.
"""

import time
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
    monkeypatch.setenv("FIRESTORE_COLLECTION", "test_state")
    reset_config()


def _make_doc_snapshot(data: dict | None, exists: bool = True):
    """Build a mock Firestore DocumentSnapshot."""
    snap = MagicMock()
    snap.exists = exists
    snap.to_dict.return_value = data if exists else None
    snap.id = "mock_id"
    return snap


def _make_state_manager():
    """Create a StateManager with a fully mocked Firestore client."""
    from state_manager import StateManager

    mock_db = MagicMock()
    sm = StateManager(db=mock_db)
    return sm, mock_db


# ---------------------------------------------------------------------------
# Page token
# ---------------------------------------------------------------------------


class TestPageToken:
    """Tests for page_token get/set round-trip."""

    def test_get_page_token_when_exists(self):
        sm, mock_db = _make_state_manager()
        snap = _make_doc_snapshot({"token": "abc123"})
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        assert sm.get_page_token() == "abc123"

    def test_get_page_token_when_missing(self):
        sm, mock_db = _make_state_manager()
        snap = _make_doc_snapshot(None, exists=False)
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        assert sm.get_page_token() is None

    def test_set_page_token(self):
        sm, mock_db = _make_state_manager()
        set_mock = (
            mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.set
        )

        sm.set_page_token("new_token")
        set_mock.assert_called_once_with({"token": "new_token"})


# ---------------------------------------------------------------------------
# Watch channel
# ---------------------------------------------------------------------------


class TestWatchChannel:
    """Tests for watch channel CRUD."""

    def test_get_watch_channel_exists(self):
        sm, mock_db = _make_state_manager()
        data = {"channel_id": "ch1", "resource_id": "res1", "expiration": 9999}
        snap = _make_doc_snapshot(data)
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        result = sm.get_watch_channel()
        assert result == data

    def test_get_watch_channel_missing(self):
        sm, mock_db = _make_state_manager()
        snap = _make_doc_snapshot(None, exists=False)
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        assert sm.get_watch_channel() is None

    def test_set_watch_channel(self):
        sm, mock_db = _make_state_manager()
        set_mock = (
            mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.set
        )

        sm.set_watch_channel("ch1", "res1", 9999)
        set_mock.assert_called_once_with(
            {
                "channel_id": "ch1",
                "resource_id": "res1",
                "expiration": 9999,
            }
        )

    def test_clear_watch_channel(self):
        sm, mock_db = _make_state_manager()
        delete_mock = (
            mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.delete
        )

        sm.clear_watch_channel()
        delete_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Distributed lock
# ---------------------------------------------------------------------------


class TestAcquireLock:
    """Tests for the distributed lock mechanism."""

    def test_acquire_lock_succeeds_when_unlocked(self):
        sm, mock_db = _make_state_manager()
        snap = _make_doc_snapshot(None, exists=False)

        # The transactional decorator calls the inner function with a transaction.
        # We need to mock the entire transaction flow.
        lock_ref = MagicMock()
        sm._config_ref = MagicMock(return_value=lock_ref)
        lock_ref.get.return_value = snap

        # Mock firestore.transactional to just call the function
        with patch("state_manager.firestore") as mock_fs:
            mock_fs.transactional = lambda fn: fn
            mock_db.transaction.return_value = MagicMock()

            result = sm.acquire_lock()
            assert result is True

    def test_acquire_lock_fails_when_held_and_not_stale(self):
        sm, mock_db = _make_state_manager()
        lock_data = {
            "locked": True,
            "owner": "other-owner",
            "acquired_at": time.time(),  # just now, not stale
        }
        snap = _make_doc_snapshot(lock_data)

        lock_ref = MagicMock()
        sm._config_ref = MagicMock(return_value=lock_ref)
        lock_ref.get.return_value = snap

        with patch("state_manager.firestore") as mock_fs:
            mock_fs.transactional = lambda fn: fn
            mock_db.transaction.return_value = MagicMock()

            result = sm.acquire_lock()
            assert result is False

    def test_acquire_lock_breaks_stale_lock(self):
        sm, mock_db = _make_state_manager()
        lock_data = {
            "locked": True,
            "owner": "old-owner",
            "acquired_at": time.time() - 700,  # 700s ago, stale (> 600)
        }
        snap = _make_doc_snapshot(lock_data)

        lock_ref = MagicMock()
        sm._config_ref = MagicMock(return_value=lock_ref)
        lock_ref.get.return_value = snap

        with patch("state_manager.firestore") as mock_fs:
            mock_fs.transactional = lambda fn: fn
            transaction = MagicMock()
            mock_db.transaction.return_value = transaction

            result = sm.acquire_lock()
            assert result is True
            # Should have written new lock data
            transaction.set.assert_called_once()


# ---------------------------------------------------------------------------
# Release lock
# ---------------------------------------------------------------------------


class TestReleaseLock:
    """Tests for releasing the distributed lock."""

    def test_release_lock_when_owner_matches(self):
        sm, mock_db = _make_state_manager()
        lock_data = {"locked": True, "owner": sm.lock_owner, "acquired_at": time.time()}
        snap = _make_doc_snapshot(lock_data)

        lock_ref = MagicMock()
        lock_ref.get.return_value = snap
        sm._config_ref = MagicMock(return_value=lock_ref)

        sm.release_lock()
        lock_ref.set.assert_called_once_with(
            {
                "locked": False,
                "owner": None,
                "acquired_at": None,
            }
        )

    def test_release_lock_noop_when_different_owner(self):
        sm, mock_db = _make_state_manager()
        lock_data = {"locked": True, "owner": "someone-else", "acquired_at": time.time()}
        snap = _make_doc_snapshot(lock_data)

        lock_ref = MagicMock()
        lock_ref.get.return_value = snap
        sm._config_ref = MagicMock(return_value=lock_ref)

        sm.release_lock()
        lock_ref.set.assert_not_called()

    def test_release_lock_noop_when_no_lock(self):
        sm, mock_db = _make_state_manager()
        snap = _make_doc_snapshot(None, exists=False)

        lock_ref = MagicMock()
        lock_ref.get.return_value = snap
        sm._config_ref = MagicMock(return_value=lock_ref)

        sm.release_lock()
        lock_ref.set.assert_not_called()


# ---------------------------------------------------------------------------
# File tracking CRUD
# ---------------------------------------------------------------------------


class TestFileTracking:
    """Tests for file CRUD operations."""

    def test_set_and_get_file(self):
        sm, mock_db = _make_state_manager()

        # set_file
        set_mock = (
            mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.set
        )
        data = {"name": "report.docx", "path": "Reports/report.docx", "md5": "abc"}
        sm.set_file("file1", data)
        set_mock.assert_called_once_with(data)

    def test_get_file_exists(self):
        sm, mock_db = _make_state_manager()
        snap = _make_doc_snapshot({"name": "report.docx", "path": "Reports/report.docx"})
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        result = sm.get_file("file1")
        assert result["name"] == "report.docx"

    def test_get_file_missing(self):
        sm, mock_db = _make_state_manager()
        snap = _make_doc_snapshot(None, exists=False)
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        assert sm.get_file("nonexistent") is None

    def test_delete_file(self):
        sm, mock_db = _make_state_manager()
        delete_mock = (
            mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.delete
        )

        sm.delete_file("file1")
        delete_mock.assert_called_once()

    def test_get_all_files(self):
        sm, mock_db = _make_state_manager()

        doc1 = MagicMock()
        doc1.id = "f1"
        doc1.to_dict.return_value = {"name": "a.docx"}

        doc2 = MagicMock()
        doc2.id = "f2"
        doc2.to_dict.return_value = {"name": "b.pdf"}

        mock_db.collection.return_value.document.return_value.collection.return_value.stream.return_value = [
            doc1,
            doc2,
        ]

        result = sm.get_all_files()
        assert result == {"f1": {"name": "a.docx"}, "f2": {"name": "b.pdf"}}

    def test_get_files_in_folder(self):
        sm, mock_db = _make_state_manager()

        doc1 = MagicMock()
        doc1.id = "f1"
        doc1.to_dict.return_value = {"name": "a.docx", "path": "Reports/a.docx"}

        doc2 = MagicMock()
        doc2.id = "f2"
        doc2.to_dict.return_value = {"name": "b.pdf", "path": "Archive/b.pdf"}

        mock_db.collection.return_value.document.return_value.collection.return_value.stream.return_value = [
            doc1,
            doc2,
        ]

        result = sm.get_files_in_folder("Reports/")
        assert "f1" in result
        assert "f2" not in result

    def test_clear_all_files(self):
        sm, mock_db = _make_state_manager()

        doc1 = MagicMock()
        doc2 = MagicMock()
        mock_db.collection.return_value.document.return_value.collection.return_value.stream.return_value = [
            doc1,
            doc2,
        ]

        sm.clear_all_files()
        doc1.reference.delete.assert_called_once()
        doc2.reference.delete.assert_called_once()

    def test_clear_all_files_empty(self):
        sm, mock_db = _make_state_manager()
        mock_db.collection.return_value.document.return_value.collection.return_value.stream.return_value = []

        sm.clear_all_files()
        # No error when there are no files to delete

    def test_get_file_by_target_found(self):
        sm, mock_db = _make_state_manager()

        doc = MagicMock()
        doc.id = "shortcut1"
        doc.to_dict.return_value = {"name": "link.gdoc", "target_id": "target123"}

        query_mock = MagicMock()
        query_mock.stream.return_value = [doc]
        limit_mock = MagicMock()
        limit_mock.limit.return_value = query_mock
        where_mock = MagicMock()
        where_mock.where.return_value = limit_mock
        mock_db.collection.return_value.document.return_value.collection.return_value = where_mock

        result = sm.get_file_by_target("target123")
        assert result == ("shortcut1", {"name": "link.gdoc", "target_id": "target123"})

    def test_get_file_by_target_not_found(self):
        sm, mock_db = _make_state_manager()

        query_mock = MagicMock()
        query_mock.stream.return_value = iter([])
        limit_mock = MagicMock()
        limit_mock.limit.return_value = query_mock
        where_mock = MagicMock()
        where_mock.where.return_value = limit_mock
        mock_db.collection.return_value.document.return_value.collection.return_value = where_mock

        result = sm.get_file_by_target("nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# Resync flag
# ---------------------------------------------------------------------------


class TestResyncFlag:
    """Tests for the resync_needed flag (set/clear/is)."""

    def test_set_resync_needed(self):
        sm, mock_db = _make_state_manager()
        set_mock = (
            mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.set
        )

        sm.set_resync_needed()
        set_mock.assert_called_once_with({"needed": True})

    def test_clear_resync_needed(self):
        sm, mock_db = _make_state_manager()
        delete_mock = (
            mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.delete
        )

        sm.clear_resync_needed()
        delete_mock.assert_called_once()

    def test_is_resync_needed_returns_true_when_set(self):
        sm, mock_db = _make_state_manager()
        snap = _make_doc_snapshot({"needed": True})
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        assert sm.is_resync_needed() is True

    def test_is_resync_needed_returns_false_when_not_set(self):
        sm, mock_db = _make_state_manager()
        snap = _make_doc_snapshot(None, exists=False)
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        assert sm.is_resync_needed() is False

    def test_is_resync_needed_returns_false_when_needed_is_false(self):
        sm, mock_db = _make_state_manager()
        snap = _make_doc_snapshot({"needed": False})
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        assert sm.is_resync_needed() is False


# ---------------------------------------------------------------------------
# Deferred deletes flag
# ---------------------------------------------------------------------------


class TestDeferredDeletes:
    """Tests for the deferred_deletes flag (set/has/clear)."""

    def test_set_deferred_deletes(self):
        sm, mock_db = _make_state_manager()
        set_mock = (
            mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.set
        )

        sm.set_deferred_deletes()
        set_mock.assert_called_once_with({"deferred": True})

    def test_has_deferred_deletes_returns_true_when_set(self):
        sm, mock_db = _make_state_manager()
        snap = _make_doc_snapshot({"deferred": True})
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        assert sm.has_deferred_deletes() is True

    def test_has_deferred_deletes_returns_false_when_not_set(self):
        sm, mock_db = _make_state_manager()
        snap = _make_doc_snapshot(None, exists=False)
        mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = snap

        assert sm.has_deferred_deletes() is False

    def test_clear_deferred_deletes(self):
        sm, mock_db = _make_state_manager()
        delete_mock = (
            mock_db.collection.return_value.document.return_value.collection.return_value.document.return_value.delete
        )

        sm.clear_deferred_deletes()
        delete_mock.assert_called_once()
