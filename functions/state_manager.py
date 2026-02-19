"""Firestore state management with distributed locking.

Schema (subcollection design):
  {collection}/config/page_token    — incremental changes cursor
  {collection}/config/watch_channel — channel_id, resource_id, expiration
  {collection}/config/sync_lock     — locked, owner, acquired_at
  {collection}/files/{fileId}       — name, path, md5, mime_type, etc.
"""

import logging
import time
import uuid

from google.cloud import firestore

from config import get_config

logger = logging.getLogger(__name__)

LOCK_TTL_SECONDS = 600  # 10 minutes


class LockError(Exception):
    pass


class StateManager:
    def __init__(self, db: firestore.Client | None = None):
        self.cfg = get_config()
        self.db = db or firestore.Client(project=self.cfg.gcp_project)
        self.collection = self.cfg.firestore_collection
        self.lock_owner = str(uuid.uuid4())

    # --- Config documents ---

    def _config_ref(self, doc_id: str):
        return self.db.collection(self.collection).document("config").collection("settings").document(doc_id)

    def get_page_token(self) -> str | None:
        doc = self._config_ref("page_token").get()
        return doc.to_dict().get("token") if doc.exists else None

    def set_page_token(self, token: str):
        self._config_ref("page_token").set({"token": token})

    def get_watch_channel(self) -> dict | None:
        doc = self._config_ref("watch_channel").get()
        return doc.to_dict() if doc.exists else None

    def set_watch_channel(self, channel_id: str, resource_id: str, expiration: int):
        self._config_ref("watch_channel").set({
            "channel_id": channel_id,
            "resource_id": resource_id,
            "expiration": expiration,
        })

    def clear_watch_channel(self):
        self._config_ref("watch_channel").delete()

    # --- Distributed lock ---

    def acquire_lock(self) -> bool:
        """Try to acquire the sync lock. Returns True if acquired."""
        lock_ref = self._config_ref("sync_lock")

        @firestore.transactional
        def _acquire(transaction):
            doc = lock_ref.get(transaction=transaction)
            if doc.exists:
                data = doc.to_dict()
                if data.get("locked"):
                    acquired_at = data.get("acquired_at", 0)
                    if time.time() - acquired_at < LOCK_TTL_SECONDS:
                        return False  # Lock held and not stale
                    logger.warning(
                        f"Breaking stale lock from {data.get('owner')} "
                        f"(acquired {time.time() - acquired_at:.0f}s ago)"
                    )

            transaction.set(lock_ref, {
                "locked": True,
                "owner": self.lock_owner,
                "acquired_at": time.time(),
            })
            return True

        transaction = self.db.transaction()
        return _acquire(transaction)

    def release_lock(self):
        """Release the sync lock if we own it."""
        lock_ref = self._config_ref("sync_lock")
        doc = lock_ref.get()
        if doc.exists and doc.to_dict().get("owner") == self.lock_owner:
            lock_ref.set({"locked": False, "owner": None, "acquired_at": None})

    # --- File tracking (subcollection) ---

    def _file_ref(self, file_id: str):
        return self.db.collection(self.collection).document("files").collection("tracked").document(file_id)

    def get_file(self, file_id: str) -> dict | None:
        doc = self._file_ref(file_id).get()
        return doc.to_dict() if doc.exists else None

    def set_file(self, file_id: str, data: dict):
        self._file_ref(file_id).set(data)

    def delete_file(self, file_id: str):
        self._file_ref(file_id).delete()

    def get_all_files(self) -> dict[str, dict]:
        """Return all tracked files as {file_id: data}."""
        docs = self.db.collection(self.collection).document("files").collection("tracked").stream()
        return {doc.id: doc.to_dict() for doc in docs}

    def get_files_in_folder(self, folder_path: str) -> dict[str, dict]:
        """Return all tracked files whose path starts with folder_path."""
        all_files = self.get_all_files()
        return {
            fid: data for fid, data in all_files.items()
            if data.get("path", "").startswith(folder_path)
        }
