"""Tests for functions/drive_client.py."""

from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from config import reset_config


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    """Provide required environment variables for Config."""
    monkeypatch.setenv("GCP_PROJECT", "test-project")
    monkeypatch.setenv("DRIVE_FOLDER_ID", "folder123")
    monkeypatch.setenv("GIT_REPO_URL", "https://github.com/test/repo.git")
    monkeypatch.setenv("GIT_BRANCH", "main")
    monkeypatch.setenv("GIT_TOKEN_SECRET", "git-token")
    reset_config()


class TestListAllFilesShortcuts:
    """Tests for shortcut handling in recursive listing."""

    def test_folder_shortcut_is_skipped(self):
        """Folder shortcuts should be skipped during initial recursive listing."""
        from drive_client import FOLDER_MIME, SHORTCUT_MIME, DriveClient

        mock_service = MagicMock()
        mock_files = mock_service.files.return_value
        mock_files.list.return_value.execute.return_value = {
            "files": [
                {
                    "id": "shortcut-folder",
                    "name": "Team Docs",
                    "mimeType": SHORTCUT_MIME,
                    "shortcutDetails": {
                        "targetId": "target-folder",
                        "targetMimeType": FOLDER_MIME,
                    },
                },
                {
                    "id": "file1",
                    "name": "report.docx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                },
            ]
        }

        client = DriveClient(service=mock_service)
        result = client.list_all_files()

        assert [item["id"] for item in result] == ["file1"]
        assert mock_files.list.call_count == 1

    def test_file_shortcut_is_resolved(self):
        """File shortcuts should resolve to target metadata and be included."""
        from drive_client import SHORTCUT_MIME, DriveClient

        mock_service = MagicMock()
        mock_files = mock_service.files.return_value
        shortcut_file = {
            "id": "shortcut-file",
            "name": "Link to Spec",
            "mimeType": SHORTCUT_MIME,
            "shortcutDetails": {
                "targetId": "target-doc",
                "targetMimeType": "application/vnd.google-apps.document",
            },
        }
        mock_files.list.return_value.execute.return_value = {"files": [shortcut_file]}

        client = DriveClient(service=mock_service)
        resolved = {
            "id": "shortcut-file",
            "name": "Link to Spec",
            "mimeType": "application/vnd.google-apps.document",
            "_target_id": "target-doc",
        }
        client.resolve_shortcut = MagicMock(return_value=resolved)

        result = client.list_all_files()

        client.resolve_shortcut.assert_called_once_with(shortcut_file)
        assert result == [resolved]


def _make_http_error(status: int) -> HttpError:
    """Build an HttpError with the given status code."""
    return HttpError(httplib2.Response({"status": status}), b"error")


class TestVerifyFileDeleted:
    """Unit tests for DriveClient.verify_file_deleted.

    These test the actual method (not a mock), verifying that each HTTP
    response is mapped to the correct True/False return value.
    """

    def _make_client(self):
        from drive_client import DriveClient

        mock_service = MagicMock()
        client = DriveClient(service=mock_service)
        return client, mock_service

    def test_file_exists_not_trashed(self):
        """Active file → return False (not deleted)."""
        client, svc = self._make_client()
        svc.files.return_value.get.return_value.execute.return_value = {"trashed": False}
        assert client.verify_file_deleted("f1") is False

    def test_file_exists_trashed(self):
        """Trashed file → return True."""
        client, svc = self._make_client()
        svc.files.return_value.get.return_value.execute.return_value = {"trashed": True}
        assert client.verify_file_deleted("f1") is True

    def test_404_permanently_deleted(self):
        """404 = permanently deleted → return True."""
        client, svc = self._make_client()
        svc.files.return_value.get.return_value.execute.side_effect = _make_http_error(404)
        assert client.verify_file_deleted("f1") is True

    def test_403_lost_access_should_not_confirm_delete(self):
        """403 = lost access, NOT confirmed deleted.

        For shared folders the service account can transiently lose access
        to individual files.  Treating 403 as 'deleted' caused the
        delete/re-add oscillation observed on 2026-03-11.
        """
        client, svc = self._make_client()
        svc.files.return_value.get.return_value.execute.side_effect = _make_http_error(403)
        # 403 must return False — the file may still exist
        assert client.verify_file_deleted("f1") is False

    def test_500_transient_error(self):
        """500 = transient server error → return False."""
        client, svc = self._make_client()
        svc.files.return_value.get.return_value.execute.side_effect = _make_http_error(500)
        assert client.verify_file_deleted("f1") is False

    def test_429_rate_limit(self):
        """429 = rate limited → return False."""
        client, svc = self._make_client()
        svc.files.return_value.get.return_value.execute.side_effect = _make_http_error(429)
        assert client.verify_file_deleted("f1") is False

    def test_network_error(self):
        """Generic exception (network timeout, etc.) → return False."""
        client, svc = self._make_client()
        svc.files.return_value.get.return_value.execute.side_effect = ConnectionError("timeout")
        assert client.verify_file_deleted("f1") is False
