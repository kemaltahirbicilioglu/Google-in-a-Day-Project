"""Crawler engine: multi-threaded BFS with back-pressure and rate limiting.

Each CrawlerJob manages a pool of worker threads that pull URLs from a bounded
queue, fetch pages via urllib.request, parse HTML with html.parser, and feed
the results into the shared IndexStore.
"""

import queue
import ssl
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from crawler.html_parser import parse_html
from storage.index_store import IndexStore
from storage.state_store import StateStore


class CrawlerJob:
    """A single crawl operation with its own worker pool.

    Args:
        crawler_id: Unique identifier for this job.
        origin: Starting URL.
        max_depth: Maximum hops from origin (the 'k' parameter).
        index_store: Shared inverted index.
        state_store: Persistence layer for state/logs.
        max_workers: Number of concurrent fetch threads.
        hit_rate: Maximum global requests per second.
        max_queue_size: Bounded queue capacity (back-pressure).
        max_pages: Stop after crawling this many pages (0 = unlimited).
    """

    def __init__(
        self,
        crawler_id: str,
        origin: str,
        max_depth: int,
        index_store: IndexStore,
        state_store: StateStore,
        max_workers: int = 4,
        hit_rate: float = 2.0,
        max_queue_size: int = 10000,
        max_pages: int = 500,
    ) -> None:
        self.crawler_id = crawler_id
        self.origin = origin
        self.max_depth = max_depth
        self.max_workers = max_workers
        self.hit_rate = hit_rate
        self.max_queue_size = max_queue_size
        self.max_pages = max_pages

        self._index_store = index_store
        self._state_store = state_store

        self._url_queue: queue.Queue[tuple[str, int]] = queue.Queue(maxsize=max_queue_size)
        self._visited: set[str] = set()
        self._visited_lock = threading.Lock()

        self._pages_crawled = 0
        self._counter_lock = threading.Lock()
        self._backpressure_events = 0

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused initially

        self._rate_interval = 1.0 / max(hit_rate, 0.01)
        self._rate_lock = threading.Lock()
        self._last_request_time = 0.0

        self._workers: list[threading.Thread] = []
        self._coordinator: threading.Thread | None = None
        self._status = "created"
        self._created_at = datetime.now(timezone.utc).isoformat()
        self._started_at: str | None = None
        self._completed_at: str | None = None

        self._flush_every_n = 10
        self._ssl_ctx = self._build_ssl_context()
        self._ssl_ctx_fallback = self._build_ssl_context(permissive=True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, resume_items: list[tuple[str, int]] | None = None) -> None:
        """Start the crawl. Optionally seed with persisted queue items for resume."""
        self._status = "running"
        self._started_at = datetime.now(timezone.utc).isoformat()

        self._visited = self._state_store.load_visited_urls()
        self._log(f"Loaded {len(self._visited)} previously visited URLs")

        if resume_items:
            for url, depth in resume_items:
                try:
                    self._url_queue.put_nowait((url, depth))
                except queue.Full:
                    break
            self._log(f"Resumed with {self._url_queue.qsize()} queued URLs")
        else:
            self._url_queue.put_nowait((self.origin, 0))

        self._save_meta()

        self._coordinator = threading.Thread(
            target=self._coordinate, daemon=True, name=f"crawler-{self.crawler_id}"
        )
        self._coordinator.start()

    def stop(self) -> None:
        """Signal all workers to stop and persist remaining state."""
        self._stop_event.set()
        self._pause_event.set()  # unblock if paused
        self._status = "stopped"
        self._log("Stop signal sent")
        self._persist_queue()
        self._index_store.flush_to_disk()
        self._save_meta()

    def pause(self) -> None:
        """Pause all workers (they block until resumed)."""
        self._pause_event.clear()
        self._status = "paused"
        self._log("Paused")
        self._persist_queue()
        self._save_meta()

    def resume(self) -> None:
        """Resume paused workers."""
        self._pause_event.set()
        self._status = "running"
        self._log("Resumed")
        self._save_meta()

    @property
    def is_alive(self) -> bool:
        return self._coordinator is not None and self._coordinator.is_alive()

    # ------------------------------------------------------------------
    # Status snapshot
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return a snapshot of the crawler's current state."""
        live_status = self._status
        if self._status == "running" and not self.is_alive:
            live_status = "finished"
        return {
            "crawler_id": self.crawler_id,
            "origin": self.origin,
            "max_depth": self.max_depth,
            "status": live_status,
            "pages_crawled": self._pages_crawled,
            "queue_depth": self._url_queue.qsize(),
            "max_queue_size": self.max_queue_size,
            "max_workers": self.max_workers,
            "hit_rate": self.hit_rate,
            "max_pages": self.max_pages,
            "backpressure_events": self._backpressure_events,
            "created_at": self._created_at,
            "started_at": self._started_at,
            "completed_at": self._completed_at,
        }

    # ------------------------------------------------------------------
    # Coordinator thread
    # ------------------------------------------------------------------

    def _coordinate(self) -> None:
        """Spawn workers, wait for them all to finish, then clean up."""
        self._log(
            f"Starting {self.max_workers} workers for {self.origin} "
            f"(depth={self.max_depth})"
        )

        for i in range(self.max_workers):
            t = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name=f"worker-{self.crawler_id}-{i}",
            )
            self._workers.append(t)
            t.start()

        for t in self._workers:
            t.join()

        self._index_store.flush_to_disk()
        self._completed_at = datetime.now(timezone.utc).isoformat()
        if self._status != "stopped":
            self._status = "finished"
        self._log(f"Crawl complete: {self._pages_crawled} pages indexed")
        self._save_meta()

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        """Pull URLs from the queue and crawl them until done or stopped."""
        idle_rounds = 0
        while not self._stop_event.is_set():
            self._pause_event.wait()

            if self.max_pages > 0 and self._pages_crawled >= self.max_pages:
                break

            try:
                url, depth = self._url_queue.get(timeout=2)
                idle_rounds = 0
            except queue.Empty:
                idle_rounds += 1
                if idle_rounds >= 3:
                    break
                continue

            try:
                self._crawl_url(url, depth)
            except Exception as exc:
                self._log(f"Error crawling {url}: {exc}")
            finally:
                self._url_queue.task_done()

            if self._pages_crawled % self._flush_every_n == 0 and self._pages_crawled > 0:
                self._index_store.flush_to_disk()
                self._persist_queue()

    # ------------------------------------------------------------------
    # Crawl a single URL
    # ------------------------------------------------------------------

    def _crawl_url(self, url: str, depth: int) -> None:
        """Fetch, parse, index, and discover links for one URL."""
        with self._visited_lock:
            if url in self._visited:
                return
            self._visited.add(url)

        if depth > self.max_depth:
            return

        self._rate_limit()

        html = self._fetch(url)
        if html is None:
            return

        result = parse_html(html, url)

        self._index_store.add_page(
            word_frequencies=result.word_frequencies,
            page_url=url,
            origin_url=self.origin,
            depth=depth,
        )
        self._state_store.append_visited_url(url, self.crawler_id)

        with self._counter_lock:
            self._pages_crawled += 1
            count = self._pages_crawled

        if count % 5 == 0 or count == 1:
            self._log(
                f"Progress: {count} pages crawled, "
                f"queue depth: {self._url_queue.qsize()}"
            )

        if depth < self.max_depth:
            self._enqueue_links(result.links, depth + 1)

    def _enqueue_links(self, links: list[str], depth: int) -> None:
        """Add discovered links to the queue with back-pressure handling."""
        for link in links:
            if self._stop_event.is_set():
                break
            with self._visited_lock:
                if link in self._visited:
                    continue
            try:
                self._url_queue.put((link, depth), timeout=1)
            except queue.Full:
                self._backpressure_events += 1
                if self._backpressure_events % 10 == 1:
                    self._log(
                        f"Back-pressure: queue full ({self.max_queue_size}), "
                        f"dropping links (event #{self._backpressure_events})"
                    )
                break

    # ------------------------------------------------------------------
    # HTTP fetch with rate limiting
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        """Enforce the global hit rate across all workers."""
        with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._rate_interval:
                time.sleep(self._rate_interval - elapsed)
            self._last_request_time = time.monotonic()

    def _fetch(self, url: str) -> str | None:
        """Fetch a URL and return HTML content, or None on failure."""
        headers = {"User-Agent": "GoogleInADay-Crawler/1.0"}
        req = urllib.request.Request(url, headers=headers)

        for ctx in (self._ssl_ctx, self._ssl_ctx_fallback):
            try:
                with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                    if resp.status != 200:
                        self._log(f"HTTP {resp.status} for {url}")
                        return None
                    raw = resp.read(2 * 1024 * 1024)  # 2 MB cap
                    content_type = resp.headers.get("Content-Type", "")
                    if "html" not in content_type.lower() and "text" not in content_type.lower():
                        return None
                    try:
                        return raw.decode("utf-8")
                    except UnicodeDecodeError:
                        return raw.decode("latin-1", errors="replace")
            except ssl.SSLError:
                continue
            except Exception as exc:
                self._log(f"Fetch error for {url}: {exc}")
                return None
        return None

    @staticmethod
    def _build_ssl_context(permissive: bool = False) -> ssl.SSLContext:
        if permissive:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return ssl.create_default_context()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _persist_queue(self) -> None:
        """Snapshot the current queue to disk for resumability."""
        items: list[tuple[str, int]] = []
        try:
            while True:
                items.append(self._url_queue.get_nowait())
        except queue.Empty:
            pass
        self._state_store.save_queue(self.crawler_id, items)
        for item in items:
            try:
                self._url_queue.put_nowait(item)
            except queue.Full:
                break

    def _save_meta(self) -> None:
        self._state_store.save_crawler_meta(self.crawler_id, self.get_status())

    def _log(self, message: str) -> None:
        self._state_store.append_log(self.crawler_id, message)
