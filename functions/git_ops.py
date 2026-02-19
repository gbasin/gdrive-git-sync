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

    def _run(self, args: list[str], cwd: str | None = None, env: dict | None = None) -> str:
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
            logger.error(f"Git command failed: {' '.join(args)}\nstderr: {result.stderr}")
            raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
        return result.stdout

    def clone(self):
        """Clone the repo with partial clone (blob filter)."""
        auth_url = self._auth_url()
        self._run(
            ["git", "clone", "--filter=blob:none", "--branch", self.cfg.git_branch, auth_url, self.repo_path],
            cwd=self.work_dir,
        )
        logger.info(f"Cloned repo to {self.repo_path}")

    def write_file(self, rel_path: str, content: bytes):
        """Write a file to the repo working tree."""
        full_path = os.path.join(self.repo_path, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(content)
        self._run(["git", "add", rel_path])

    def write_text_file(self, rel_path: str, content: str):
        """Write a text file to the repo working tree."""
        self.write_file(rel_path, content.encode("utf-8"))

    def rename_file(self, old_path: str, new_path: str):
        """Rename/move a file using git mv."""
        new_full = os.path.join(self.repo_path, new_path)
        os.makedirs(os.path.dirname(new_full), exist_ok=True)
        self._run(["git", "mv", old_path, new_path])

    def delete_file(self, rel_path: str):
        """Delete a file using git rm."""
        full_path = os.path.join(self.repo_path, rel_path)
        if os.path.exists(full_path):
            self._run(["git", "rm", "-f", rel_path])

    def stage_file(self, rel_path: str):
        """Stage a specific file (git add)."""
        self._run(["git", "add", rel_path])

    def unstage_all(self):
        """Unstage all staged changes (git reset HEAD)."""
        import contextlib

        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(["git", "reset", "HEAD"])

    def has_staged_changes(self) -> bool:
        """Check if there are staged changes."""
        try:
            self._run(["git", "diff", "--cached", "--quiet"])
            return False
        except subprocess.CalledProcessError:
            return True  # Return code 1 means there are differences

    def commit(self, message: str, author_name: str, author_email: str):
        """Create a commit with the given author."""
        author = f"{author_name} <{author_email}>"
        self._run(["git", "commit", "-m", message, f"--author={author}"])
        logger.info(f"Committed: {message[:80]}... (author: {author})")

    def push(self):
        """Push all commits to remote."""
        self._run(["git", "push", "origin", self.cfg.git_branch])
        logger.info("Pushed to remote")

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

    def cleanup(self):
        """Remove the working directory."""
        if os.path.exists(self.work_dir):
            shutil.rmtree(self.work_dir, ignore_errors=True)
            logger.info(f"Cleaned up {self.work_dir}")
