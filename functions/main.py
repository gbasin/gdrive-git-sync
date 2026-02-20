"""Cloud Function entry points for Google Drive → Git sync.

Three functions:
  sync_handler  — webhook receiver for Drive push notifications
  renew_watch   — scheduled channel renewal (every 6 days)
  setup_watch   — one-time initialization + domain verification
"""

import json
import logging
import os

import functions_framework
from flask import Request

from drive_client import DriveClient
from git_ops import GitRepo
from state_manager import StateManager
from sync_engine import run_initial_sync, run_sync

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Google domain verification file content (set via env var during bootstrap)
VERIFICATION_TOKEN = os.environ.get("GOOGLE_VERIFICATION_TOKEN", "")


@functions_framework.http
def sync_handler(request: Request):
    """Receive Drive push notifications and sync changes to git.

    Always returns 200 to prevent Google from retrying.
    POST = webhook notification
    GET = domain verification
    """
    # GET: serve domain verification file
    if request.method == "GET":
        if VERIFICATION_TOKEN:
            return f"google-site-verification: {VERIFICATION_TOKEN}", 200, {"Content-Type": "text/html"}
        return "OK", 200

    # POST: handle Drive notification
    try:
        # Validate this is from our watch channel
        channel_id = request.headers.get("X-Goog-Channel-ID", "")
        resource_state = request.headers.get("X-Goog-Resource-State", "")

        logger.info(f"Webhook received: state={resource_state}, channel={channel_id[:8]}...")

        # "sync" state is the initial validation ping — just ACK it
        if resource_state == "sync":
            logger.info("Received sync validation ping")
            return "OK", 200

        # Validate channel ID matches our stored channel
        state = StateManager()
        watch_info = state.get_watch_channel()
        if watch_info and watch_info.get("channel_id") != channel_id:
            logger.warning(f"Unknown channel ID: {channel_id}")
            return "OK", 200

        # Try to acquire lock
        if not state.acquire_lock():
            # Flag that changes arrived while sync was running.
            # The active sync will check this flag and re-run before exiting.
            state.set_resync_needed()
            logger.info("Another sync is in progress, flagged for resync")
            return "OK", 200

        try:
            _run_sync_loop(state)
        finally:
            state.release_lock()

    except Exception:
        logger.exception("Sync handler failed")

    # Always return 200
    return "OK", 200


def _run_sync_loop(state: StateManager, max_iterations: int = 3):
    """Run sync, then re-run if more webhooks arrived during processing.

    Caps iterations to prevent unbounded looping from continuous edits.
    After max_iterations, any remaining changes wait for the next webhook
    or the 4-hour safety-net.
    """
    for i in range(max_iterations):
        state.clear_resync_needed()
        repo = GitRepo()
        try:
            drive = DriveClient()
            count = run_sync(drive, state, repo)
            logger.info(f"Sync iteration {i + 1}: {count} changes")
        finally:
            repo.cleanup()

        if not state.is_resync_needed():
            break
        logger.info("Resync flag set, running again...")


@functions_framework.http
def renew_watch(request: Request):
    """Renew the Drive watch channel. Called by Cloud Scheduler every 6 days.

    1. Stop old channel
    2. Create new channel
    3. Run a catchup sync for any gap
    """
    try:
        state = StateManager()
        drive = DriveClient()

        # Stop old channel
        old_channel = state.get_watch_channel()
        if old_channel:
            drive.stop_watch_channel(
                old_channel["channel_id"],
                old_channel["resource_id"],
            )
            state.clear_watch_channel()
            logger.info("Stopped old watch channel")

        # Get current page token
        page_token = state.get_page_token()
        if not page_token:
            page_token = drive.get_start_page_token()
            state.set_page_token(page_token)

        # Determine webhook URL (same function URL)
        function_url = os.environ.get("SYNC_HANDLER_URL")
        if not function_url:
            return "SYNC_HANDLER_URL not configured", 500

        # Create new channel
        channel_info = drive.setup_watch_channel(function_url, page_token)
        state.set_watch_channel(
            channel_info["channel_id"],
            channel_info["resource_id"],
            channel_info["expiration"],
        )
        logger.info(f"Created new watch channel, expires {channel_info['expiration']}")

        # Catchup sync
        if state.acquire_lock():
            repo = GitRepo()
            try:
                count = run_sync(drive, state, repo)
                logger.info(f"Catchup sync: {count} changes")
            finally:
                repo.cleanup()
                state.release_lock()

        return json.dumps({"status": "renewed", "channel_id": channel_info["channel_id"]}), 200

    except Exception:
        logger.exception("Renew watch failed")
        return "Internal error", 500


@functions_framework.http
def setup_watch(request: Request):
    """One-time initialization: get start page token, create watch channel.

    GET: Serve domain verification HTML
    POST: Initialize watch channel
      ?initial_sync=true: Also run a full initial sync
    """
    # GET: serve verification
    if request.method == "GET":
        if VERIFICATION_TOKEN:
            return f"google-site-verification: {VERIFICATION_TOKEN}", 200, {"Content-Type": "text/html"}
        return "Setup endpoint. POST to initialize.", 200

    try:
        state = StateManager()
        drive = DriveClient()

        # Get start page token
        page_token = drive.get_start_page_token()
        state.set_page_token(page_token)
        logger.info(f"Stored initial page token: {page_token}")

        # Create watch channel
        function_url = os.environ.get("SYNC_HANDLER_URL")
        if not function_url:
            return "SYNC_HANDLER_URL not configured", 500

        channel_info = drive.setup_watch_channel(function_url, page_token)
        state.set_watch_channel(
            channel_info["channel_id"],
            channel_info["resource_id"],
            channel_info["expiration"],
        )
        logger.info(f"Watch channel created: {channel_info['channel_id']}")

        result = {
            "status": "initialized",
            "channel_id": channel_info["channel_id"],
            "expiration": channel_info["expiration"],
        }

        # Optional initial sync — uses full folder listing, not the
        # delta/changes feed, so it picks up files that already existed
        # before the page token was captured.
        initial_sync = request.args.get("initial_sync", "false").lower() == "true"
        if initial_sync:
            logger.info("Running initial sync...")
            if state.acquire_lock():
                repo = GitRepo()
                try:
                    count = run_initial_sync(drive, state, repo)
                    result["initial_sync_count"] = count
                finally:
                    repo.cleanup()
                    state.release_lock()

        return json.dumps(result), 200

    except Exception:
        logger.exception("Setup watch failed")
        return "Internal error", 500
