"""Crawler state persistence: visited URLs, queue snapshots, and job metadata.

All file I/O is isolated here so the crawler engine stays storage-agnostic.
"""

import json
import os
import shutil
import threading
from datetime import datetime, timezone
from typing import Any


_DATA_DIR = "data"


class StateStore:
    """Manages on-disk state for crawler jobs."""

    def __init__(self, data_dir: str = _DATA_DIR) -> None:
        self._data_dir = data_dir
        self._crawlers_dir = os.path.join(data_dir, "crawlers")
        self._visited_file = os.path.join(data_dir, "visited_urls.data")
        self._visited_lock = threading.Lock()
        os.makedirs(self._crawlers_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Visited URLs
    # ------------------------------------------------------------------

    def load_visited_urls(self) -> set[str]:
        """Load the global visited-URL set from disk."""
        visited: set[str] = set()
        if not os.path.isfile(self._visited_file):
            return visited
        try:
            with open(self._visited_file, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split("\t")
                    if parts:
                        visited.add(parts[0])
        except OSError:
            pass
        return visited

    def append_visited_url(self, url: str, crawler_id: str) -> None:
        """Append a single visited URL to the global file (thread-safe)."""
        with self._visited_lock:
            os.makedirs(self._data_dir, exist_ok=True)
            with open(self._visited_file, "a", encoding="utf-8") as f:
                ts = datetime.now(timezone.utc).isoformat()
                f.write(f"{url}\t{crawler_id}\t{ts}\n")

    # ------------------------------------------------------------------
    # Crawler metadata (JSON)
    # ------------------------------------------------------------------

    def _meta_path(self, crawler_id: str) -> str:
        return os.path.join(self._crawlers_dir, f"{crawler_id}.json")

    def save_crawler_meta(self, crawler_id: str, meta: dict[str, Any]) -> None:
        """Write crawler metadata to a JSON file."""
        path = self._meta_path(crawler_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, default=str)

    def load_crawler_meta(self, crawler_id: str) -> dict[str, Any] | None:
        """Read crawler metadata from disk. Returns None if not found."""
        path = self._meta_path(crawler_id)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def list_crawler_ids(self) -> list[str]:
        """Return all crawler IDs found on disk."""
        if not os.path.isdir(self._crawlers_dir):
            return []
        ids: list[str] = []
        for fname in os.listdir(self._crawlers_dir):
            if fname.endswith(".json"):
                ids.append(fname[:-5])
        return ids

    # ------------------------------------------------------------------
    # Queue snapshots
    # ------------------------------------------------------------------

    def _queue_path(self, crawler_id: str) -> str:
        return os.path.join(self._crawlers_dir, f"{crawler_id}.queue")

    def save_queue(self, crawler_id: str, items: list[tuple[str, int]]) -> None:
        """Persist the remaining queue to disk for resumability.

        Args:
            crawler_id: The crawler this queue belongs to.
            items: List of (url, depth) tuples.
        """
        path = self._queue_path(crawler_id)
        with open(path, "w", encoding="utf-8") as f:
            for url, depth in items:
                f.write(f"{url}\t{depth}\n")

    def load_queue(self, crawler_id: str) -> list[tuple[str, int]]:
        """Load a persisted queue from disk."""
        path = self._queue_path(crawler_id)
        items: list[tuple[str, int]] = []
        if not os.path.isfile(path):
            return items
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split("\t")
                    if len(parts) == 2:
                        try:
                            items.append((parts[0], int(parts[1])))
                        except ValueError:
                            continue
        except OSError:
            pass
        return items

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def _log_path(self, crawler_id: str) -> str:
        return os.path.join(self._crawlers_dir, f"{crawler_id}.log")

    def append_log(self, crawler_id: str, message: str) -> None:
        """Append a timestamped log line for a crawler."""
        path = self._log_path(crawler_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")

    def read_logs(self, crawler_id: str, tail: int = 100) -> list[str]:
        """Read the last *tail* log lines for a crawler."""
        path = self._log_path(crawler_id)
        if not os.path.isfile(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return [line.rstrip("\n") for line in lines[-tail:]]
        except OSError:
            return []

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def clear_all(self) -> None:
        """Remove all data files (visited, crawlers, storage)."""
        if os.path.isdir(self._data_dir):
            shutil.rmtree(self._data_dir)
        os.makedirs(self._crawlers_dir, exist_ok=True)
