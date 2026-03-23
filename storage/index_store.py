"""Thread-safe inverted index with letter-sharded file persistence.

The index lives in memory for fast reads and is periodically flushed to disk.
Each word shard is a file named by its first letter (a.data ... z.data, other.data).
Line format (TAB-separated): word  relevant_url  origin_url  depth  frequency
"""

import os
import threading
from typing import NamedTuple


class IndexEntry(NamedTuple):
    """A single posting in the inverted index."""
    word: str
    relevant_url: str
    origin_url: str
    depth: int
    frequency: int


_STORAGE_DIR = os.path.join("data", "storage")


class IndexStore:
    """Thread-safe inverted index backed by letter-sharded files on disk."""

    def __init__(self, storage_dir: str = _STORAGE_DIR) -> None:
        self._storage_dir = storage_dir
        self._lock = threading.RLock()
        self._index: dict[str, list[IndexEntry]] = {}
        self._dirty_letters: set[str] = set()
        self._total_entries = 0
        os.makedirs(self._storage_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_page(
        self,
        word_frequencies: dict[str, int],
        page_url: str,
        origin_url: str,
        depth: int,
    ) -> None:
        """Index all words from a crawled page.

        Args:
            word_frequencies: Mapping of word -> count extracted from the page.
            page_url: The URL the content was fetched from.
            origin_url: The origin URL that started this crawl job.
            depth: Hop distance from the origin.
        """
        with self._lock:
            for word, freq in word_frequencies.items():
                if len(word) < 2:
                    continue
                entry = IndexEntry(
                    word=word,
                    relevant_url=page_url,
                    origin_url=origin_url,
                    depth=depth,
                    frequency=freq,
                )
                self._index.setdefault(word, []).append(entry)
                self._dirty_letters.add(self._shard_key(word))
                self._total_entries += 1

    def search(self, words: list[str]) -> list[IndexEntry]:
        """Return all index entries matching any of the given words.

        Supports exact matches and prefix matching for words >= 3 chars.
        """
        results: list[IndexEntry] = []
        with self._lock:
            for query_word in words:
                q = query_word.lower()
                if q in self._index:
                    results.extend(self._index[q])
                if len(q) >= 3:
                    for indexed_word, entries in self._index.items():
                        if indexed_word != q and indexed_word.startswith(q):
                            results.extend(entries)
        return results

    def flush_to_disk(self) -> None:
        """Write all dirty shards to disk."""
        with self._lock:
            dirty = set(self._dirty_letters)
            self._dirty_letters.clear()

        for letter in dirty:
            self._write_shard(letter)

    def load_from_disk(self) -> None:
        """Reload the full index from disk files into memory."""
        with self._lock:
            self._index.clear()
            self._total_entries = 0
            if not os.path.isdir(self._storage_dir):
                return
            for fname in os.listdir(self._storage_dir):
                if not fname.endswith(".data"):
                    continue
                path = os.path.join(self._storage_dir, fname)
                self._read_shard(path)

    @property
    def total_entries(self) -> int:
        with self._lock:
            return self._total_entries

    @property
    def unique_words(self) -> int:
        with self._lock:
            return len(self._index)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _shard_key(word: str) -> str:
        first = word[0].lower() if word else "other"
        return first if first.isalpha() else "other"

    def _shard_path(self, letter: str) -> str:
        return os.path.join(self._storage_dir, f"{letter}.data")

    def _write_shard(self, letter: str) -> None:
        """Rewrite a single shard file with all entries whose words start with that letter."""
        entries: list[IndexEntry] = []
        with self._lock:
            for word, word_entries in self._index.items():
                if self._shard_key(word) == letter:
                    entries.extend(word_entries)

        entries.sort(key=lambda e: (e.word, -e.frequency))
        path = self._shard_path(letter)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(
                    f"{e.word}\t{e.relevant_url}\t{e.origin_url}\t{e.depth}\t{e.frequency}\n"
                )

    def _read_shard(self, path: str) -> None:
        """Read a shard file into the in-memory index. Caller must hold _lock."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    parts = line.split("\t")
                    if len(parts) != 5:
                        continue
                    word, relevant_url, origin_url, depth_str, freq_str = parts
                    try:
                        entry = IndexEntry(
                            word=word,
                            relevant_url=relevant_url,
                            origin_url=origin_url,
                            depth=int(depth_str),
                            frequency=int(freq_str),
                        )
                    except ValueError:
                        continue
                    self._index.setdefault(word, []).append(entry)
                    self._total_entries += 1
        except OSError:
            pass
