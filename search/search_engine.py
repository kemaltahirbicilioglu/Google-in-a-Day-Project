"""Search engine: query normalization, relevance scoring, and pagination.

Reads from the shared in-memory IndexStore so results reflect the latest
crawled data even while indexing is still active.
"""

import math
import re
from typing import Any

from storage.index_store import IndexEntry, IndexStore


_WORD_RE = re.compile(r"\b[a-zA-Z]{2,}\b")


class SearchEngine:
    """Stateless search interface over an IndexStore."""

    def __init__(self, index_store: IndexStore) -> None:
        self._index = index_store

    def search(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Run a search query and return scored, paginated results.

        Args:
            query: Raw search string from the user.
            limit: Maximum results to return.
            offset: Pagination offset.

        Returns:
            Dict with keys: query, total, results (list of result dicts).
        """
        words = self._normalize(query)
        if not words:
            return {"query": query, "total": 0, "results": []}

        total_docs = self._index.total_documents
        df_map = {w: self._index.document_frequency(w) for w in words}

        raw_entries = self._index.search(words)
        scored = self._score_and_dedup(
            raw_entries, words, total_docs, df_map, self._index,
        )
        scored.sort(key=lambda r: r["score"], reverse=True)

        total = len(scored)
        page = scored[offset: offset + limit]

        return {
            "query": query,
            "total": total,
            "results": page,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(query: str) -> list[str]:
        """Extract lowercase alphabetic tokens (2+ chars) from the query."""
        return _WORD_RE.findall(query.lower())

    @staticmethod
    def _score_and_dedup(
        entries: list[IndexEntry],
        query_words: list[str],
        total_docs: int,
        df_map: dict[str, int],
        index_store: IndexStore,
    ) -> list[dict[str, Any]]:
        """Score entries using TF-IDF with URL signals and depth decay.

        Per matched word in a document:
            TF   = 1 + log10(frequency)           (sublinear — dampens high counts)
            IDF  = log10((N + 1) / (df + 1)) + 1  (smoothed inverse document freq)
            norm = log10(doc_word_count + 1)       (length normalization)
            base = (TF * IDF / norm) * 100

        Prefix-only matches are discounted to 30 %.

        Per-document aggregation:
            exact_bonus = +25 % of raw score when exact query word present
            url_bonus   = +20 % of raw score when query word appears in URL
            depth_decay = 1 / (1 + 0.1 * depth)   (smooth exponential penalty)
            final       = (raw + exact_bonus + url_bonus) * depth_decay
        """
        query_set = set(query_words)

        url_scores: dict[str, float] = {}
        url_has_exact: dict[str, bool] = {}
        url_meta: dict[str, dict[str, Any]] = {}

        for entry in entries:
            url = entry.relevant_url
            doc_size = index_store.get_doc_size(url)

            tf = 1.0 + math.log10(max(entry.frequency, 1))
            norm = math.log10(max(doc_size, 2))

            df = df_map.get(entry.word, 1)
            for qw in query_words:
                if entry.word.startswith(qw) and qw in df_map:
                    df = df_map[qw]
                    break
            idf = math.log10((total_docs + 1) / (max(df, 1) + 1)) + 1.0

            base = (tf * idf / norm) * 100.0

            exact = entry.word in query_set
            word_score = base if exact else base * 0.3

            url_scores[url] = url_scores.get(url, 0.0) + word_score

            if exact:
                url_has_exact[url] = True

            if url not in url_meta or entry.depth < url_meta[url]["depth"]:
                url_meta[url] = {
                    "relevant_url": url,
                    "origin_url": entry.origin_url,
                    "depth": entry.depth,
                }

        results: list[dict[str, Any]] = []
        for url, raw_score in url_scores.items():
            meta = url_meta[url]

            exact_bonus = raw_score * 0.25 if url_has_exact.get(url, False) else 0.0
            url_lower = url.lower()
            url_bonus = raw_score * 0.2 if any(qw in url_lower for qw in query_words) else 0.0
            depth_factor = 1.0 / (1.0 + meta["depth"] * 0.1)

            score = round(
                max((raw_score + exact_bonus + url_bonus) * depth_factor, 0.0), 4,
            )
            results.append({**meta, "score": score})

        return results
