"""Configuration from environment variables with defaults and validation."""

import os


class ConfigError(Exception):
    pass


class Config:
    def __init__(self):
        # Required
        self.gcp_project = self._require("GCP_PROJECT")
        self.drive_folder_id = self._require("DRIVE_FOLDER_ID")
        self.git_repo_url = self._require("GIT_REPO_URL")
        self.git_branch = self._require("GIT_BRANCH")
        self.git_token_secret = self._require("GIT_TOKEN_SECRET")

        # Optional
        self.exclude_paths = self._parse_list(os.environ.get("EXCLUDE_PATHS", ""))
        self.skip_extensions = self._parse_list(
            os.environ.get("SKIP_EXTENSIONS", ".zip,.exe,.dmg,.iso")
        )
        self.max_file_size_mb = int(os.environ.get("MAX_FILE_SIZE_MB", "100"))
        self.commit_author_name = os.environ.get(
            "COMMIT_AUTHOR_NAME", "Drive Sync Bot"
        )
        self.commit_author_email = os.environ.get(
            "COMMIT_AUTHOR_EMAIL", "sync@example.com"
        )
        self.firestore_collection = os.environ.get(
            "FIRESTORE_COLLECTION", "drive_sync_state"
        )
        self.docs_subdir = os.environ.get("DOCS_SUBDIR", "docs")

    def _require(self, name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise ConfigError(f"Required environment variable {name} is not set")
        return value

    def _parse_list(self, value: str) -> list[str]:
        if not value.strip():
            return []
        return [item.strip() for item in value.split(",") if item.strip()]


_config = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config():
    """Reset cached config (for testing)."""
    global _config
    _config = None
