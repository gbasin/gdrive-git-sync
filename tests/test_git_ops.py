"""Tests for functions/git_ops.py.

subprocess and Secret Manager are mocked throughout.
"""

import os
import subprocess
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
    reset_config()


@pytest.fixture
def mock_secret_manager():
    """Patch Secret Manager to return a fake token."""
    with patch("git_ops.secretmanager") as mock_sm:
        client = MagicMock()
        mock_sm.SecretManagerServiceClient.return_value = client
        response = MagicMock()
        response.payload.data = b"ghp_faketoken123"
        client.access_secret_version.return_value = response
        yield client


@pytest.fixture
def git_repo(mock_secret_manager, tmp_path):
    """Create a GitRepo instance with a temp work directory."""
    from git_ops import GitRepo

    repo = GitRepo(work_dir=str(tmp_path))
    return repo


# ---------------------------------------------------------------------------
# _auth_url
# ---------------------------------------------------------------------------


class TestAuthUrl:
    """Tests for token insertion into the git URL."""

    def test_inserts_token_into_https_url(self, git_repo):
        url = git_repo._auth_url()
        assert url == "https://oauth2:ghp_faketoken123@github.com/test/repo.git"

    def test_rejects_non_https_url(self, git_repo, monkeypatch):
        monkeypatch.setenv("GIT_REPO_URL", "git@github.com:test/repo.git")
        reset_config()
        # Re-read config
        from git_ops import GitRepo

        repo = GitRepo(work_dir=str(git_repo.work_dir))
        repo._token = "fake"

        with pytest.raises(ValueError, match="Unsupported git URL scheme"):
            repo._auth_url()

    def test_token_is_cached(self, git_repo, mock_secret_manager):
        # Call twice
        git_repo._auth_url()
        git_repo._auth_url()
        # Secret Manager should only be called once
        assert mock_secret_manager.access_secret_version.call_count == 1


# ---------------------------------------------------------------------------
# clone
# ---------------------------------------------------------------------------


class TestClone:
    """Tests for git clone."""

    @patch("git_ops.subprocess.run")
    def test_clone_uses_filter_flag(self, mock_run, git_repo):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        git_repo.clone()

        args = mock_run.call_args[0][0]
        assert "git" == args[0]
        assert "clone" == args[1]
        assert "--filter=blob:none" in args
        assert "--branch" in args
        assert "main" in args
        # Auth URL should be embedded
        assert any("oauth2:ghp_faketoken123@" in a for a in args)

    @patch("git_ops.subprocess.run")
    def test_clone_targets_work_dir(self, mock_run, git_repo):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        git_repo.clone()

        # cwd should be work_dir (not repo_path)
        assert mock_run.call_args[1]["cwd"] == git_repo.work_dir


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class TestWriteFile:
    """Tests for writing and staging files."""

    @patch("git_ops.subprocess.run")
    def test_write_creates_file_and_stages(self, mock_run, git_repo):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create the repo directory so write_file can work
        os.makedirs(git_repo.repo_path, exist_ok=True)

        git_repo.write_file("docs/test.txt", b"hello world")

        # File should exist
        full_path = os.path.join(git_repo.repo_path, "docs/test.txt")
        assert os.path.exists(full_path)
        with open(full_path, "rb") as f:
            assert f.read() == b"hello world"

        # git add should have been called
        args = mock_run.call_args[0][0]
        assert args == ["git", "add", "docs/test.txt"]


# ---------------------------------------------------------------------------
# rename_file
# ---------------------------------------------------------------------------


class TestRenameFile:
    """Tests for git mv."""

    @patch("git_ops.subprocess.run")
    def test_rename_calls_git_mv(self, mock_run, git_repo):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create repo dir for makedirs call
        os.makedirs(git_repo.repo_path, exist_ok=True)

        git_repo.rename_file("old/file.txt", "new/file.txt")

        args = mock_run.call_args[0][0]
        assert args == ["git", "mv", "old/file.txt", "new/file.txt"]


# ---------------------------------------------------------------------------
# delete_file
# ---------------------------------------------------------------------------


class TestDeleteFile:
    """Tests for git rm."""

    @patch("git_ops.subprocess.run")
    def test_delete_calls_git_rm(self, mock_run, git_repo):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create a file to delete
        os.makedirs(git_repo.repo_path, exist_ok=True)
        file_path = os.path.join(git_repo.repo_path, "doomed.txt")
        with open(file_path, "w") as f:
            f.write("bye")

        git_repo.delete_file("doomed.txt")

        args = mock_run.call_args[0][0]
        assert args == ["git", "rm", "-f", "doomed.txt"]

    @patch("git_ops.subprocess.run")
    def test_delete_noop_when_file_missing(self, mock_run, git_repo):
        os.makedirs(git_repo.repo_path, exist_ok=True)

        git_repo.delete_file("nonexistent.txt")

        # git rm should not be called
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# has_staged_changes
# ---------------------------------------------------------------------------


class TestHasStagedChanges:
    """Tests for staged changes detection."""

    @patch("git_ops.subprocess.run")
    def test_returns_false_when_no_changes(self, mock_run, git_repo):
        # git diff --cached --quiet exits 0 when no changes
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        assert git_repo.has_staged_changes() is False

    @patch("git_ops.subprocess.run")
    def test_returns_true_when_changes_present(self, mock_run, git_repo):
        # git diff --cached --quiet exits 1 when there are changes
        mock_run.return_value = MagicMock(returncode=1, stderr="differences")
        mock_run.side_effect = subprocess.CalledProcessError(1, ["git", "diff"])

        assert git_repo.has_staged_changes() is True


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


class TestCommit:
    """Tests for creating commits."""

    @patch("git_ops.subprocess.run")
    def test_commit_includes_author_flag(self, mock_run, git_repo):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        git_repo.commit("Sync changes", "Alice", "alice@example.com")

        args = mock_run.call_args[0][0]
        assert args[0:3] == ["git", "commit", "-m"]
        assert args[3] == "Sync changes"
        assert args[4] == "--author=Alice <alice@example.com>"

    @patch("git_ops.subprocess.run")
    def test_commit_with_special_chars_in_message(self, mock_run, git_repo):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        git_repo.commit("Add file: report (final).docx", "Bob", "bob@co.com")

        args = mock_run.call_args[0][0]
        assert args[3] == "Add file: report (final).docx"


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    """Tests for work directory removal."""

    def test_cleanup_removes_work_dir(self, git_repo):
        # Ensure work dir exists
        assert os.path.exists(git_repo.work_dir)
        os.makedirs(os.path.join(git_repo.work_dir, "subdir"), exist_ok=True)

        git_repo.cleanup()

        assert not os.path.exists(git_repo.work_dir)

    def test_cleanup_noop_when_already_gone(self, git_repo, tmp_path):
        from git_ops import GitRepo

        repo = GitRepo(work_dir=str(tmp_path / "nonexistent"))
        # Should not raise
        repo.cleanup()


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


class TestPush:
    """Tests for git push."""

    @patch("git_ops.subprocess.run")
    def test_push_uses_correct_branch(self, mock_run, git_repo):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        git_repo.push()

        args = mock_run.call_args[0][0]
        assert args == ["git", "push", "origin", "main"]
