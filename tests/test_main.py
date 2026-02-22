"""Tests for functions/main.py sync_handler.

StateManager, DriveClient, GitRepo, and run_sync are mocked throughout.
Flask Request objects are built using Werkzeug's test helpers.
"""

from unittest.mock import patch

import pytest
from werkzeug.test import EnvironBuilder

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
    monkeypatch.setenv("SYNC_TRIGGER_SECRET", "test-trigger-secret")
    reset_config()


def _make_request(method="POST", headers=None):
    """Build a Flask Request from the given method and headers."""
    builder = EnvironBuilder(method=method, headers=headers or {})
    return builder.get_request()


# ---------------------------------------------------------------------------
# sync_handler — channel ID validation
# ---------------------------------------------------------------------------


class TestSyncHandlerChannelValidation:
    """Tests for X-Goog-Channel-ID validation logic in sync_handler."""

    def test_no_channel_id_with_valid_secret_proceeds_to_sync(self):
        """Channel-less request with valid trigger secret should sync."""
        from main import sync_handler

        request = _make_request(
            headers={
                "X-Goog-Resource-State": "update",
                "X-Sync-Trigger-Secret": "test-trigger-secret",
            }
        )

        with (
            patch("main.StateManager") as MockState,
            patch("main._run_sync_loop") as mock_loop,
        ):
            mock_state = MockState.return_value
            mock_state.get_watch_channel.return_value = {
                "channel_id": "stored-channel-id",
                "resource_id": "res1",
            }
            mock_state.acquire_lock.return_value = True

            body, status = sync_handler(request)

        assert status == 200
        mock_state.acquire_lock.assert_called_once()
        mock_loop.assert_called_once()

    def test_no_channel_id_without_secret_does_not_sync(self):
        """Channel-less request without trigger secret should be rejected."""
        from main import sync_handler

        request = _make_request(
            headers={
                "X-Goog-Resource-State": "update",
            }
        )

        with (
            patch("main.StateManager") as MockState,
            patch("main._run_sync_loop") as mock_loop,
        ):
            mock_state = MockState.return_value
            mock_state.get_watch_channel.return_value = {
                "channel_id": "stored-channel-id",
                "resource_id": "res1",
            }

            body, status = sync_handler(request)

        assert status == 200
        assert body == "OK"
        mock_state.acquire_lock.assert_not_called()
        mock_loop.assert_not_called()

    def test_matching_channel_id_proceeds_to_sync(self):
        """Request with a channel ID that matches the stored one should sync."""
        from main import sync_handler

        request = _make_request(
            headers={
                "X-Goog-Channel-ID": "correct-channel-id",
                "X-Goog-Resource-State": "update",
            }
        )

        with (
            patch("main.StateManager") as MockState,
            patch("main._run_sync_loop") as mock_loop,
        ):
            mock_state = MockState.return_value
            mock_state.get_watch_channel.return_value = {
                "channel_id": "correct-channel-id",
                "resource_id": "res1",
            }
            mock_state.acquire_lock.return_value = True

            body, status = sync_handler(request)

        assert status == 200
        mock_state.acquire_lock.assert_called_once()
        mock_loop.assert_called_once()

    def test_mismatched_channel_id_returns_200_without_syncing(self):
        """Request with a channel ID that does NOT match should return 200 immediately."""
        from main import sync_handler

        request = _make_request(
            headers={
                "X-Goog-Channel-ID": "wrong-channel-id",
                "X-Goog-Resource-State": "update",
            }
        )

        with (
            patch("main.StateManager") as MockState,
            patch("main._run_sync_loop") as mock_loop,
        ):
            mock_state = MockState.return_value
            mock_state.get_watch_channel.return_value = {
                "channel_id": "correct-channel-id",
                "resource_id": "res1",
            }

            body, status = sync_handler(request)

        assert status == 200
        assert body == "OK"
        # Should NOT attempt to acquire lock or run sync
        mock_state.acquire_lock.assert_not_called()
        mock_loop.assert_not_called()


# ---------------------------------------------------------------------------
# sync_handler — resource_state "sync" validation ping
# ---------------------------------------------------------------------------


class TestSyncHandlerValidationPing:
    """Tests for the initial 'sync' validation ping from Google."""

    def test_sync_resource_state_returns_200_without_syncing(self):
        """resource_state == 'sync' is the initial validation ping -- just ACK it."""
        from main import sync_handler

        request = _make_request(
            headers={
                "X-Goog-Channel-ID": "some-channel",
                "X-Goog-Resource-State": "sync",
            }
        )

        with (
            patch("main.StateManager") as MockState,
            patch("main._run_sync_loop") as mock_loop,
        ):
            body, status = sync_handler(request)

        assert status == 200
        assert body == "OK"
        # StateManager should never be instantiated for a sync ping
        MockState.assert_not_called()
        mock_loop.assert_not_called()


# ---------------------------------------------------------------------------
# sync_handler — GET request (domain verification)
# ---------------------------------------------------------------------------


class TestSyncHandlerGet:
    """Tests for the GET / domain verification path."""

    def test_get_request_returns_200(self):
        """GET request should return 200 (domain verification path)."""
        from main import sync_handler

        request = _make_request(method="GET")

        with (
            patch("main.StateManager") as MockState,
            patch("main._run_sync_loop") as mock_loop,
        ):
            body, status = sync_handler(request)

        assert status == 200
        # Should NOT attempt any sync logic
        MockState.assert_not_called()
        mock_loop.assert_not_called()

    @patch("main.VERIFICATION_TOKEN", "google1234abcd.html")
    def test_get_request_with_verification_token(self):
        """GET with VERIFICATION_TOKEN set should return the verification content."""
        from main import sync_handler

        request = _make_request(method="GET")

        body, status, headers = sync_handler(request)

        assert status == 200
        assert "google-site-verification: google1234abcd.html" in body
        assert headers["Content-Type"] == "text/html"
