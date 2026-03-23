# Product Requirement Document: Web Crawler & Search System

## 1. Overview

This system is a single-machine web crawler and search engine designed for high-performance indexing and retrieval. It crawls web pages starting from a given URL up to a configurable depth `k`, builds an inverted index of discovered content, and exposes a search interface that returns relevant URLs as triples `(relevant_url, origin_url, depth)`.

The system emphasizes architectural sensibility, concurrency with back-pressure management, and language-native Python implementations.

## 2. Goals

- **Index** arbitrary websites via BFS traversal to a configurable depth `k`.
- **Search** the indexed content while crawling is still active, reflecting new results in real time.
- **Back-pressure** to prevent unbounded memory growth and overwhelming target servers.
- **Resumability** so interrupted crawls can continue without re-crawling visited pages.
- **Observability** via a lightweight web UI showing queue depth, worker status, and indexed page count.

## 3. Technology Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Language | Python 3.10+ | Required by assignment |
| Concurrency | `threading` + `queue.Queue` | Language-native worker pool with bounded back-pressure |
| HTTP client | `urllib.request` | Standard library, no heavy dependencies |
| HTML parsing | `html.parser` | Standard library, avoids BeautifulSoup/lxml |
| Web framework | FastAPI + uvicorn | Lightweight, async-capable, auto-generated docs |
| Storage | File-based (TAB-separated `.data`, JSON) | No external DB required, runs on localhost |
| Frontend | Vanilla HTML/CSS/JS | No build step, served as static files |

## 4. API Specification

### 4.1 POST /index — Start a Crawl

**Request:**
```json
{
  "origin": "https://example.com",
  "k": 3,
  "max_workers": 4,
  "hit_rate": 2.0,
  "max_queue_size": 10000,
  "max_pages": 500
}
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| origin | string (URL) | yes | — | Starting URL for the crawl |
| k | int | yes | — | Maximum hop depth from origin |
| max_workers | int | no | 4 | Number of concurrent fetch threads |
| hit_rate | float | no | 2.0 | Maximum requests per second (global) |
| max_queue_size | int | no | 10000 | Bounded queue capacity (back-pressure trigger) |
| max_pages | int | no | 500 | Maximum total pages to crawl (0 = unlimited) |

**Response (201 Created):**
```json
{
  "crawler_id": "1711234567_12345",
  "status": "started",
  "origin": "https://example.com",
  "k": 3
}
```

### 4.2 GET /search — Query the Index

**Request:** `GET /search?query=python+tutorial&limit=20&offset=0`

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | yes | — | Space-separated search terms |
| limit | int | no | 20 | Results per page |
| offset | int | no | 0 | Pagination offset |

**Response (200 OK):**
```json
{
  "query": "python tutorial",
  "total": 42,
  "results": [
    {
      "relevant_url": "https://example.com/python",
      "origin_url": "https://example.com",
      "depth": 2,
      "score": 0.85
    }
  ]
}
```

### 4.3 GET /status — System Overview

**Response (200 OK):**
```json
{
  "crawlers": [
    {
      "crawler_id": "1711234567_12345",
      "status": "running",
      "origin": "https://example.com",
      "pages_crawled": 127,
      "queue_depth": 342,
      "max_queue_size": 10000,
      "backpressure_events": 3,
      "elapsed_seconds": 45.2
    }
  ],
  "total_indexed_pages": 127,
  "total_indexed_words": 8542
}
```

### 4.4 GET /status/{crawler_id} — Single Crawler Detail

Returns the same shape as one element of the `crawlers` array above, plus a `logs` field (last 50 lines).

### 4.5 Crawler Control

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/stop/{crawler_id}` | POST | Gracefully stop a running crawler |
| `/pause/{crawler_id}` | POST | Pause (workers block until resumed) |
| `/resume/{crawler_id}` | POST | Resume a paused or interrupted crawler |

## 5. Concurrency Model

### 5.1 Worker Pool Architecture

```
POST /index
    │
    ▼
┌─────────────────────────────┐
│  Coordinator Thread          │
│  - Seeds queue with origin   │
│  - Spawns N worker threads   │
│  - Waits for completion      │
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│  Bounded Queue               │
│  queue.Queue(maxsize=N)      │
│  Items: (url, depth) tuples  │
│  BACK-PRESSURE: put() blocks │
│  when queue is full           │
└─────────┬───────────────────┘
          │
    ┌─────┼─────┬─────┐
    ▼     ▼     ▼     ▼
  Worker Worker Worker Worker
    │     │     │     │
    └──┬──┘     └──┬──┘
       ▼           ▼
   Rate Limiter (shared)
       │
       ▼
   urllib.request → HTML → html.parser
       │
       ├── Words → IndexStore (in-memory + disk)
       └── Links → Queue (with back-pressure)
```

1. The coordinator seeds the queue with `(origin_url, depth=0)`.
2. N worker threads pull `(url, depth)` tuples from the bounded queue.
3. Each worker: rate-limit → fetch → parse HTML → index words → enqueue child links.
4. If `depth + 1 > k`, child links are not enqueued.
5. If the queue is full, `put(timeout=1)` triggers back-pressure — the worker logs the event and drops excess links for that page.

### 5.2 Thread Safety

| Shared Resource | Protection | Access Pattern |
|----------------|------------|----------------|
| URL queue | `queue.Queue` (inherently thread-safe) | Multiple producers/consumers |
| Visited URL set | `threading.Lock` | Check-and-add before enqueue |
| Inverted index | `threading.RLock` | Concurrent reads, serialized writes |
| Page counter | `threading.Lock` | Atomic increment |
| Rate limiter | `threading.Lock` | Serialized token acquisition |

## 6. Back-Pressure Design

Back-pressure operates at two independent levels:

### 6.1 Queue Depth Limit
- `queue.Queue(maxsize=max_queue_size)` blocks `put()` when full.
- Workers that discover new links call `put(timeout=1)` — if the queue is still full after 1 second, the link is dropped and a back-pressure event is logged.
- This prevents unbounded memory growth regardless of how link-dense the crawled pages are.

### 6.2 Rate Limiter
- A shared lock enforces a minimum interval of `1 / hit_rate` seconds between HTTP requests.
- This limits the aggregate request rate across all workers, ensuring politeness toward target servers.

### 6.3 Observability
The `/status` endpoint exposes:
- `queue_depth` — current items in queue
- `max_queue_size` — capacity
- `backpressure_events` — how many times the queue was full
- `pages_crawled` — total fetched so far

## 7. Data Storage Format

### 7.1 Inverted Index — Letter-Sharded Files

Location: `data/storage/{letter}.data` (a.data … z.data, other.data)

Each line is TAB-separated:
```
word    relevant_url    origin_url    depth    frequency
```

At runtime, the index lives in memory (`dict[str, list[IndexEntry]]`) for fast reads. It is flushed to disk every 10 pages and on crawl completion/pause/stop.

### 7.2 Visited URLs

Location: `data/visited_urls.data`

Each line: `url\tcrawler_id\ttimestamp` (append-only)

### 7.3 Crawler State

Location: `data/crawlers/{crawler_id}.json`

JSON containing: origin, k, config params, status, timestamps, pages_crawled.

### 7.4 Queue Snapshot

Location: `data/crawlers/{crawler_id}.queue`

Each line: `url\tdepth` — persisted periodically and on pause/stop for resumability.

### 7.5 Logs

Location: `data/crawlers/{crawler_id}.log`

Timestamped text lines, append-only.

## 8. Resumability Strategy

When a crawl is interrupted (user stop, process crash, or pause):
1. **Visited URLs** are already persisted to `visited_urls.data` (appended after each fetch).
2. **Queue snapshot** is saved to `{crawler_id}.queue` periodically and on stop/pause.
3. **Crawler config** is in `{crawler_id}.json`.
4. **Inverted index** is flushed to `data/storage/*.data`.

On resume (`POST /resume/{crawler_id}`):
1. Load config from `.json`.
2. Reload visited set from `visited_urls.data` (deduplication).
3. Reload queue from `.queue` file.
4. Reload inverted index from `data/storage/*.data`.
5. Restart worker pool — picks up exactly where it left off.

## 9. Search Relevance

Scoring formula per result:
```
score = (frequency × 10) + (1000 if exact_match) - (depth × 5)
```

- **Term frequency (TF):** Higher word count on a page → higher score.
- **Depth penalty:** Pages closer to the origin rank higher.
- **Exact match bonus:** Exact query word matches beat prefix matches.
- **Deduplication:** Results grouped by `relevant_url`, keeping highest score.
- **Prefix matching:** For query terms ≥ 3 characters, indexed words starting with the query term are also matched (with lower score).

## 10. UI Requirements

A single-page web dashboard served as static files, with three sections:

### 10.1 Crawler Section
- Form: origin URL, depth k, optional advanced params (workers, hit rate, queue size, max pages).
- "Start Crawl" button → POST /index.
- List of past/active crawlers with status badges.

### 10.2 Status Section
- Real-time display (polling every 2 seconds) for each active crawler:
  - Queue depth gauge (current / max)
  - Pages crawled counter
  - Back-pressure events counter
  - Worker count
  - Log tail (last N lines)
- Pause / Resume / Stop controls.

### 10.3 Search Section
- Search bar with submit button.
- Paginated results table: relevant_url, origin_url, depth, score.
- Works while indexing is active (reflects latest results).

## 11. Non-Functional Requirements

- **Performance:** Handle crawls of 1000+ pages on a single machine.
- **Memory:** Bounded by queue size; inverted index flushed to disk periodically.
- **Robustness:** Graceful handling of HTTP errors, timeouts, malformed HTML, SSL certificate issues.
- **No heavy frameworks:** Core logic uses only `urllib.request`, `html.parser`, `queue.Queue`, `threading`.
- **Localhost only:** No external database or services required.
