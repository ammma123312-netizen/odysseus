#!/usr/bin/env python3
"""HF Spaces ↔ HF Dataset persistence sync.

Free-tier Spaces have ephemeral storage: every restart wipes /app/data.
This script bridges that gap by snapshotting /app/data to a private HF
Dataset repo on a 5-minute cadence, and restoring it on boot.

Modes:
    pull   — one-shot: download latest snapshot into /app/data (boot)
    push   — one-shot: upload /app/data into the dataset (manual)
    watch  — long-running: push every PERSIST_INTERVAL seconds (background)

Required env:
    HF_TOKEN            — write-scope token for the dataset
    PERSIST_REPO_ID     — e.g. "Ejdjdososs/odysseus-data"
    PERSIST_DATA_DIR    — defaults to /app/data
    PERSIST_INTERVAL    — seconds between pushes (default 300)

Safety:
    - Pull is skipped if /app/data is non-empty AND already contains app.db
      (treat existing local data as the source of truth — avoids overwriting
      mid-session if HF dataset lags behind).
    - Push uploads as a tarball (data.tar.gz) so SQLite WAL + journal stay
      consistent. We snapshot under a lock-friendly copy first.
    - All errors log but never crash the watcher — the app must stay up.
"""
from __future__ import annotations

import os
import sys
import time
import shutil
import tarfile
import tempfile
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [hf-persist] %(levelname)s: %(message)s",
)
log = logging.getLogger("hf_persistence")


REPO_ID = os.environ.get("PERSIST_REPO_ID", "Ejdjdososs/odysseus-data")
DATA_DIR = Path(os.environ.get("PERSIST_DATA_DIR", "/app/data"))
INTERVAL = int(os.environ.get("PERSIST_INTERVAL", "300"))
ARCHIVE_NAME = "data.tar.gz"
TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")


def _api():
    """Lazy import — keeps script importable without huggingface_hub installed."""
    from huggingface_hub import HfApi
    return HfApi(token=TOKEN)


def _has_existing_db() -> bool:
    return (DATA_DIR / "app.db").exists()


def pull() -> int:
    """Restore /app/data from the dataset's latest snapshot."""
    if not TOKEN:
        log.warning("HF_TOKEN not set — skipping pull (persistence disabled)")
        return 0

    if _has_existing_db():
        log.info("Local app.db already present — skipping pull to avoid clobber")
        return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import hf_hub_download
        from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError
    except ImportError:
        log.error("huggingface_hub not installed — persistence disabled")
        return 1

    try:
        archive_path = hf_hub_download(
            repo_id=REPO_ID,
            filename=ARCHIVE_NAME,
            repo_type="dataset",
            token=TOKEN,
            local_dir=tempfile.gettempdir(),
        )
    except EntryNotFoundError:
        log.info("No snapshot yet in dataset — first boot, starting fresh")
        return 0
    except RepositoryNotFoundError:
        log.error("Dataset %s not found — check PERSIST_REPO_ID", REPO_ID)
        return 1
    except Exception as e:  # noqa: BLE001
        log.error("Pull failed (%s) — starting fresh", e)
        return 0  # Non-fatal: app must still boot

    try:
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(DATA_DIR.parent)
        log.info("Restored snapshot into %s", DATA_DIR)
    except Exception as e:  # noqa: BLE001
        log.error("Failed to extract archive: %s", e)
        return 1

    return 0


def push() -> int:
    """Snapshot /app/data and upload as data.tar.gz to the dataset."""
    if not TOKEN:
        return 0
    if not DATA_DIR.exists() or not any(DATA_DIR.iterdir()):
        log.debug("Data dir empty — nothing to push")
        return 0

    try:
        api = _api()
    except ImportError:
        return 1

    # Stage a consistent copy first. SQLite is fine to copy live IF we
    # don't tar straight off it (WAL writes mid-tar = torn archive).
    # shutil.copytree gives us a point-in-time-ish snapshot.
    with tempfile.TemporaryDirectory(prefix="hfpersist_") as tmp:
        staging = Path(tmp) / "data"
        try:
            shutil.copytree(
                DATA_DIR,
                staging,
                ignore=shutil.ignore_patterns(
                    "*.tmp", "*-journal", "*-wal", "*-shm", "*.lock",
                ),
                dirs_exist_ok=False,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("copytree had errors (continuing): %s", e)
            if not staging.exists():
                return 1

        archive_path = Path(tmp) / ARCHIVE_NAME
        try:
            with tarfile.open(archive_path, "w:gz", compresslevel=6) as tf:
                tf.add(staging, arcname="data")
        except Exception as e:  # noqa: BLE001
            log.error("Failed to create archive: %s", e)
            return 1

        size_mb = archive_path.stat().st_size / (1024 * 1024)
        try:
            api.upload_file(
                path_or_fileobj=str(archive_path),
                path_in_repo=ARCHIVE_NAME,
                repo_id=REPO_ID,
                repo_type="dataset",
                commit_message=f"snapshot {time.strftime('%Y-%m-%d %H:%M:%S')}",
            )
            log.info("Pushed snapshot (%.1f MB) to %s", size_mb, REPO_ID)
        except Exception as e:  # noqa: BLE001
            log.error("Upload failed: %s", e)
            return 1

    return 0


def watch() -> int:
    """Long-running: push every INTERVAL seconds. Survives transient errors."""
    log.info(
        "Watcher started: repo=%s interval=%ds dir=%s",
        REPO_ID, INTERVAL, DATA_DIR,
    )
    # Skip the first immediate push — let the app finish booting.
    time.sleep(min(60, INTERVAL))
    while True:
        try:
            push()
        except Exception as e:  # noqa: BLE001
            log.error("Watcher iteration crashed (will retry): %s", e)
        time.sleep(INTERVAL)


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "watch"
    if mode == "pull":
        return pull()
    if mode == "push":
        return push()
    if mode == "watch":
        return watch()
    log.error("Unknown mode: %s (expected pull|push|watch)", mode)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
