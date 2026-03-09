"""Git operations via HTTPS + token auth.

Uses partial clone (--filter=blob:none) instead of shallow clone.
Token fetched from Secret Manager at invocation time.
"""

import logging
import os
import shutil
import subprocess
import tempfile

from google.cloud import secretmanager

from config import get_config

logger = logging.getLogger(__name__)


class GitRepo:
    def __init__(self, work_dir: str | None = None):
        self.cfg = get_config()
        self.work_dir = work_dir or tempfile.mkdtemp(prefix="gdrive-sync-")
        self.repo_path = os.path.join(self.work_dir, "repo")
        self._token: str | None = None

    def _get_token(self) -> str:
        if self._token is not None:
            return self._token
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{self.cfg.gcp_project}/secrets/{self.cfg.git_token_secret}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        self._token = response.payload.data.decode("utf-8").strip()
        return self._token

    def _auth_url(self) -> str:
        """Build authenticated HTTPS URL for git operations."""
        token = self._get_token()
        url = self.cfg.git_repo_url
        # Insert token into URL: https://oauth2:TOKEN@host/path
        if url.startswith("https://"):
            return url.replace("https://", f"https://oauth2:{token}@", 1)
        raise ValueError(f"Unsupported git URL scheme: {url}")

    @staticmethod
    def _redact(text: str) -> str:
        """Replace oauth2:TOKEN@ with oauth2:***@ in any string."""
        import re

        return re.sub(r"oauth2:[^@]+@", "oauth2:***@", text)

    @staticmethod
    def _redact_args(args: list[str]) -> list[str]:
        """Replace oauth2:TOKEN@ in args with oauth2:***@ for safe logging."""
        return [GitRepo._redact(a) for a in args]

    def _run(
        self,
        args: list[str],
        cwd: str | None = None,
        env: dict | None = None,
        *,
        log_errors: bool = True,
    ) -> str:
        """Run a git command and return stdout."""
        cmd_env = os.environ.copy()
        # Prevent git from prompting for credentials
        cmd_env["GIT_TERMINAL_PROMPT"] = "0"
        if env:
            cmd_env.update(env)

        result = subprocess.run(
            args,
            cwd=cwd or self.repo_path,
            capture_output=True,
            text=True,
            env=cmd_env,
            timeout=300,
        )
        if result.returncode != 0:
            safe_args = self._redact_args(args)
            safe_stderr = self._redact(result.stderr)
            safe_stdout = self._redact(result.stdout)
            if log_errors:
                logger.error(f"Git command failed: {' '.join(safe_args)}\nstderr: {safe_stderr}")
            raise subprocess.CalledProcessError(result.returncode, safe_args, safe_stdout, safe_stderr)
        return result.stdout

    def _configure_identity(self):
        """Set committer identity (Cloud Functions has no global git config)."""
        self._run(["git", "config", "user.name", self.cfg.commit_author_name])
        self._run(["git", "config", "user.email", self.cfg.commit_author_email])

    def clone(self):
        """Clone the repo with partial clone (blob filter)."""
        auth_url = self._auth_url()
        self._run(
            ["git", "clone", "--filter=blob:none", "--branch", self.cfg.git_branch, auth_url, self.repo_path],
            cwd=self.work_dir,
        )
        self._configure_identity()
        logger.info(f"Cloned repo to {self.repo_path}")

    def clone_or_init(self):
        """Clone the repo, falling back to bare init for empty repos.

        An empty repo (zero commits) has no branches, so
        ``git clone --branch main`` fails with exit 128.  In that case
        we clone without ``--branch``/``--filter`` and create the branch
        ourselves.
        """
        auth_url = self._auth_url()
        try:
            self._run(
                ["git", "clone", "--filter=blob:none", "--branch", self.cfg.git_branch, auth_url, self.repo_path],
                cwd=self.work_dir,
            )
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").lower()
            is_empty_repo = (
                "remote branch" in stderr
                or "empty repository" in stderr
                or "does not have a commit checked out" in stderr
            )
            if not is_empty_repo:
                raise
            logger.info("Normal clone failed (empty repo), retrying without --branch")
            if os.path.exists(self.repo_path):
                shutil.rmtree(self.repo_path)
            self._run(
                ["git", "clone", auth_url, self.repo_path],
                cwd=self.work_dir,
            )
            self._run(["git", "checkout", "-b", self.cfg.git_branch])
        self._configure_identity()
        logger.info(f"Cloned repo to {self.repo_path}")

    def write_file(self, rel_path: str, content: bytes):
        """Write a file to the repo working tree."""
        full_path = os.path.join(self.repo_path, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(content)
        self._run(["git", "add", "--", rel_path])

    def write_text_file(self, rel_path: str, content: str):
        """Write a text file to the repo working tree."""
        self.write_file(rel_path, content.encode("utf-8"))

    def rename_file(self, old_path: str, new_path: str) -> bool:
        """Rename/move a file using git mv. Returns True on success."""
        old_full = os.path.join(self.repo_path, old_path)
        new_full = os.path.join(self.repo_path, new_path)
        os.makedirs(os.path.dirname(new_full), exist_ok=True)
        if os.path.exists(old_full):
            self._run(["git", "mv", "--", old_path, new_path])
            return True
        logger.warning(f"rename_file: source not found: {old_path}")
        return False

    def delete_file(self, rel_path: str):
        """Delete a file using git rm."""
        full_path = os.path.join(self.repo_path, rel_path)
        if os.path.exists(full_path):
            self._run(["git", "rm", "-f", "--", rel_path])

    def stage_file(self, rel_path: str, ignore_missing: bool = False):
        """Stage a specific file (git add).

        Works for added, modified, AND deleted files — the ``--``
        separator ensures paths that look like flags are handled
        correctly and ``-A`` covers deletions from the working tree.

        If *ignore_missing* is True, silently skip paths that don't
        exist in the working tree or index (common for old paths during
        renames/deletes in partial clones).
        """
        try:
            self._run(["git", "add", "-A", "--", rel_path], log_errors=not ignore_missing)
        except subprocess.CalledProcessError as e:
            if ignore_missing and "did not match any files" in (e.stderr or ""):
                logger.info("Skipped staging (not in tree): %s", rel_path)
            else:
                raise

    def unstage_all(self):
        """Unstage all staged changes (git reset HEAD)."""
        import contextlib

        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(["git", "reset", "HEAD"], log_errors=False)

    def has_staged_changes(self) -> bool:
        """Check if there are staged changes."""
        try:
            self._run(["git", "diff", "--cached", "--quiet"], log_errors=False)
            return False
        except subprocess.CalledProcessError as e:
            if e.returncode == 1:
                return True  # Return code 1 means there are differences
            raise  # Real errors should propagate

    def commit(self, message: str, author_name: str, author_email: str):
        """Create a commit with the given author."""
        author = f"{author_name} <{author_email}>"
        self._run(["git", "commit", "-m", message, f"--author={author}"])
        logger.info(f"Committed: {message[:80]}... (author: {author})")

    def push(self):
        """Push all commits to remote."""
        self._run(["git", "push", "origin", self.cfg.git_branch])
        logger.info("Pushed to remote")

    def push_if_ahead(self):
        """Push only if there are local commits ahead of the remote.

        Safe to call even on an empty repo or when no commits were made —
        it simply does nothing when there is nothing to push.
        """
        try:
            log_output = self._run(
                ["git", "log", "--oneline", f"origin/{self.cfg.git_branch}..HEAD"],
            )
            if not log_output.strip():
                logger.info("No new commits to push")
                return
        except subprocess.CalledProcessError:
            # origin/<branch> doesn't exist yet (empty remote) — if we
            # have *any* local commits we should push them.
            try:
                log_output = self._run(["git", "log", "--oneline", "-1"])
                if not log_output.strip():
                    logger.info("No commits at all, nothing to push")
                    return
            except subprocess.CalledProcessError:
                logger.info("No commits at all, nothing to push")
                return
        self.push()

    def commit_and_push(self, author_groups: list[dict]):
        """Create one commit per author group and push once.

        Args:
            author_groups: List of dicts with keys:
                - author_name: str
                - author_email: str
                - message: str
                - files: list of (rel_path, content_bytes | None) for staging
        """
        any_committed = False
        for group in author_groups:
            # Stage files for this author
            for rel_path, content in group.get("files", []):
                if content is None:
                    self.delete_file(rel_path)
                else:
                    self.write_file(rel_path, content)

            if self.has_staged_changes():
                self.commit(
                    group["message"],
                    group["author_name"],
                    group["author_email"],
                )
                any_committed = True

        if any_committed:
            self.push()

    def list_tracked_files(self) -> list[str]:
        """Return all tracked file paths relative to repo root."""
        output = self._run(["git", "ls-files"])
        return [line for line in output.splitlines() if line]

    def cleanup(self):
        """Remove the working directory."""
        if os.path.exists(self.work_dir):
            shutil.rmtree(self.work_dir, ignore_errors=True)
            logger.info(f"Cleaned up {self.work_dir}")
