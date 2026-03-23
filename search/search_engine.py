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
        scored = self._score_and_dedup(raw_entries, words, total_docs, df_map)
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
    ) -> list[dict[str, Any]]:
        """Score entries using TF-IDF and deduplicate by relevant_url.

        Scoring:
            TF  = 1 + log10(frequency)          (log-scaled term frequency)
            IDF = log10(total_docs / df)         (inverse document frequency)
            score = TF * IDF                     (per query term, summed)
                  + exact match bonus (1.0)
                  - depth penalty (depth * 0.1)
        """
        best: dict[str, dict[str, Any]] = {}
        query_set = set(query_words)

        for entry in entries:
            tf = 1 + math.log10(max(entry.frequency, 1))

            df = df_map.get(entry.word, 1)
            for qw in query_words:
                if entry.word.startswith(qw) and qw in df_map:
                    df = df_map[qw]
                    break
            idf = math.log10(total_docs / max(df, 1))

            tfidf = tf * idf

            exact = entry.word in query_set
            bonus = 1.0 if exact else 0.0
            penalty = entry.depth * 0.1

            score = round(tfidf + bonus - penalty, 4)
            score = max(score, 0.0)

            url = entry.relevant_url
            if url not in best or score > best[url]["score"]:
                best[url] = {
                    "relevant_url": entry.relevant_url,
                    "origin_url": entry.origin_url,
                    "depth": entry.depth,
                    "score": score,
                }

        return list(best.values())
