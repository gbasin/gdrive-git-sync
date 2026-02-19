"""Google Drive API wrapper for change detection, downloads, and watch channels."""

import fnmatch
import logging
import uuid

import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

from config import get_config
from text_extractor import GOOGLE_NATIVE_EXPORTS

logger = logging.getLogger(__name__)

# Fields to request from Drive API
CHANGE_FIELDS = (
    "nextPageToken,newStartPageToken,"
    "changes(fileId,removed,file(id,name,parents,mimeType,md5Checksum,"
    "trashed,modifiedTime,size,lastModifyingUser(displayName,emailAddress)))"
)

FILE_FIELDS = "id,name,parents,mimeType"


class DriveClient:
    def __init__(self, service=None):
        self.cfg = get_config()
        if service is None:
            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/drive.readonly"]
            )
            self.service = build("drive", "v3", credentials=creds)
        else:
            self.service = service
        # Cache for folder names (id → name)
        self._folder_cache: dict[str, str | None] = {}

    def list_changes(self, page_token: str) -> tuple[list[dict], str]:
        """Fetch all changes since page_token.

        Returns:
            (changes, new_page_token) where changes is a list of change dicts.
        """
        changes = []
        current_token = page_token

        while True:
            response = self.service.changes().list(
                pageToken=current_token,
                fields=CHANGE_FIELDS,
                spaces="drive",
                includeRemoved=True,
                pageSize=1000,
            ).execute()

            changes.extend(response.get("changes", []))

            if "nextPageToken" in response:
                current_token = response["nextPageToken"]
            else:
                new_start_token = response.get("newStartPageToken", current_token)
                return changes, new_start_token

    def get_start_page_token(self) -> str:
        """Get the current start page token for changes.list."""
        response = self.service.changes().getStartPageToken().execute()
        return response["startPageToken"]

    def is_in_folder(self, file_data: dict) -> bool:
        """Check if a file is under the monitored folder by walking parents."""
        parents = file_data.get("parents")
        if not parents:
            return False

        visited = set()
        to_check = list(parents)

        while to_check:
            parent_id = to_check.pop()
            if parent_id in visited:
                continue
            visited.add(parent_id)

            if parent_id == self.cfg.drive_folder_id:
                return True

            # Look up parent's parents
            try:
                parent = self.service.files().get(
                    fileId=parent_id, fields="parents"
                ).execute()
                parent_parents = parent.get("parents", [])
                to_check.extend(parent_parents)
            except Exception:
                logger.debug(f"Could not fetch parent {parent_id}")
                continue

        return False

    def get_file_path(self, file_data: dict) -> str:
        """Reconstruct relative path from file to DRIVE_FOLDER_ID.

        Returns path like 'Contracts/Subfolder/file.docx'
        """
        parts = []
        name = file_data.get("name", "unknown")
        parents = file_data.get("parents", [])

        if not parents:
            return name

        current_parent = parents[0]
        while current_parent and current_parent != self.cfg.drive_folder_id:
            folder_name = self._get_folder_name(current_parent)
            if folder_name is None:
                break
            parts.append(folder_name)
            # Get parent of parent
            try:
                parent_file = self.service.files().get(
                    fileId=current_parent, fields="name,parents"
                ).execute()
                parent_parents = parent_file.get("parents", [])
                current_parent = parent_parents[0] if parent_parents else None
            except Exception:
                break

        parts.reverse()
        parts.append(name)
        return "/".join(parts)

    def _get_folder_name(self, folder_id: str) -> str | None:
        if folder_id in self._folder_cache:
            return self._folder_cache[folder_id]
        try:
            result = self.service.files().get(
                fileId=folder_id, fields="name"
            ).execute()
            name = result.get("name")
            self._folder_cache[folder_id] = name
            return name
        except Exception:
            self._folder_cache[folder_id] = None
            return None

    def matches_exclude_pattern(self, relative_path: str) -> bool:
        """Check if a relative path matches any EXCLUDE_PATHS pattern."""
        for pattern in self.cfg.exclude_paths:
            if fnmatch.fnmatch(relative_path, pattern):
                return True
            # Also check if any parent dir matches
            parts = relative_path.split("/")
            for i in range(len(parts)):
                partial = "/".join(parts[: i + 1])
                if fnmatch.fnmatch(partial, pattern.rstrip("/*")):
                    return True
        return False

    def should_skip_file(self, file_data: dict) -> str | None:
        """Check if file should be skipped. Returns reason string or None."""
        name = file_data.get("name", "")
        size = file_data.get("size")
        mime_type = file_data.get("mimeType", "")

        # Check extension
        for ext in self.cfg.skip_extensions:
            if name.lower().endswith(ext.lower()):
                return f"skipped extension {ext}"

        # Check size
        if size and int(size) > self.cfg.max_file_size_mb * 1024 * 1024:
            return f"file too large ({int(size) / 1024 / 1024:.0f}MB > {self.cfg.max_file_size_mb}MB)"

        return None

    def download_file(self, file_id: str) -> bytes:
        """Download a binary file from Drive."""
        request = self.service.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue()

    def export_file(self, file_id: str, mime_type: str) -> bytes:
        """Export a Google-native file to the given MIME type."""
        request = self.service.files().export_media(
            fileId=file_id, mimeType=mime_type
        )
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue()

    def setup_watch_channel(self, webhook_url: str, page_token: str) -> dict:
        """Create a push notification channel for Drive changes.

        Returns dict with channel_id, resource_id, expiration.
        """
        channel_id = str(uuid.uuid4())
        body = {
            "id": channel_id,
            "type": "web_hook",
            "address": webhook_url,
        }
        response = self.service.changes().watch(
            pageToken=page_token,
            body=body,
            fields="resourceId,expiration",
        ).execute()

        return {
            "channel_id": channel_id,
            "resource_id": response["resourceId"],
            "expiration": int(response["expiration"]),
        }

    def stop_watch_channel(self, channel_id: str, resource_id: str):
        """Stop a push notification channel."""
        try:
            self.service.channels().stop(body={
                "id": channel_id,
                "resourceId": resource_id,
            }).execute()
            logger.info(f"Stopped watch channel {channel_id}")
        except Exception:
            logger.warning(f"Failed to stop watch channel {channel_id}", exc_info=True)
