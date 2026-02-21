"""Tests for functions/drive_client.py."""

from unittest.mock import MagicMock

import pytest

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
