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
        """Score entries with length-normalized TF-IDF and deduplicate.

        Scoring per matching word in a document:
            TF_norm  = frequency / doc_total_words  (length-normalized)
            IDF      = log10(total_docs / df)
            base     = TF_norm * IDF * 1000         (scaled for readability)

        Exact query match gets full base score; prefix-only match is
        discounted to 30% so pages *about* the exact term rank higher.

        Per-document: scores from all matched words are summed, then:
            final = sum_of_scores + exact_match_bonus (2.0) - depth * 0.05
        """
        query_set = set(query_words)

        url_scores: dict[str, float] = {}
        url_has_exact: dict[str, bool] = {}
        url_meta: dict[str, dict[str, Any]] = {}

        for entry in entries:
            url = entry.relevant_url
            doc_size = index_store.get_doc_size(url)

            tf_norm = entry.frequency / doc_size

            df = df_map.get(entry.word, 1)
            for qw in query_words:
                if entry.word.startswith(qw) and qw in df_map:
                    df = df_map[qw]
                    break
            idf = math.log10(total_docs / max(df, 1))

            base = tf_norm * idf * 1000.0

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
            exact_bonus = 2.0 if url_has_exact.get(url, False) else 0.0
            depth_penalty = meta["depth"] * 0.05
            score = round(max(raw_score + exact_bonus - depth_penalty, 0.0), 4)
            results.append({**meta, "score": score})

        return results
