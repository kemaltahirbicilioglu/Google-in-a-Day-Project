"""Search engine: query normalization, relevance scoring, and pagination.

Reads from the shared in-memory IndexStore so results reflect the latest
crawled data even while indexing is still active.
"""

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

        raw_entries = self._index.search(words)
        scored = self._score_and_dedup(raw_entries, words)
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
    ) -> list[dict[str, Any]]:
        """Score entries and deduplicate by relevant_url (keep highest score).

        Scoring formula per entry:
            base  = frequency * 10
            bonus = 1000 if the indexed word exactly matches a query word
                  + (len(matched_query_word) * 50) for prefix matches
            penalty = depth * 5

            score = base + bonus - penalty
        """
        best: dict[str, dict[str, Any]] = {}

        query_set = set(query_words)

        for entry in entries:
            exact = entry.word in query_set
            prefix_bonus = 0
            if not exact:
                for qw in query_words:
                    if entry.word.startswith(qw):
                        prefix_bonus = max(prefix_bonus, len(qw) * 50)

            score = (entry.frequency * 10) + (1000 if exact else prefix_bonus) - (entry.depth * 5)
            score = max(score, 0)

            url = entry.relevant_url
            if url not in best or score > best[url]["score"]:
                best[url] = {
                    "relevant_url": entry.relevant_url,
                    "origin_url": entry.origin_url,
                    "depth": entry.depth,
                    "score": score,
                }

        return list(best.values())
