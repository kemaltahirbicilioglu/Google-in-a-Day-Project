# Google-in-a-Day: Web Crawler & Search Engine

A single-machine web crawler and search engine built with Python. Crawls websites via BFS to a configurable depth, builds an inverted index, and exposes a search interface that works in real time -- even while indexing is still active.

## Features

- **Multi-threaded crawler** with configurable worker pool (N concurrent fetch threads)
- **Back-pressure management** via bounded queue and global rate limiter
- **Real-time search** that reflects newly indexed pages as they are crawled
- **Pause / Resume / Stop** controls for active crawlers
- **Resumability** after interruption (queue and visited URLs persisted to disk)
- **Web dashboard** with crawler form, live status monitor, and search interface
- **Language-native core** -- uses only `urllib.request`, `html.parser`, `queue.Queue`, `threading`

## Prerequisites

- Python 3.10 or higher
- pip (comes with Python)

## Quick Start

### 1. Clone and enter the project

```bash
git clone <repository-url>
cd Google-in-a-Day-Project
```

### 2. Create and activate a virtual environment

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Start the server

```bash
uvicorn app:app --port 8000
```

### 5. Open the dashboard

Navigate to **http://localhost:8000** in your browser.

## Usage

### Web Dashboard

The dashboard has three tabs:

1. **Crawler** -- Enter an origin URL and depth, then click "Start Crawl". Advanced options let you configure workers, hit rate, queue capacity, and max pages.
2. **Status** -- Real-time view of all crawlers. Shows progress bars for pages crawled and queue depth, back-pressure event counts, logs, and Pause/Resume/Stop controls. Auto-refreshes every 2 seconds.
3. **Search** -- Enter search terms and get paginated results as `(relevant_url, origin_url, depth, score)` triples.

### API Endpoints

You can also interact with the system via the REST API directly. Interactive docs are available at **http://localhost:8000/docs** (Swagger UI).

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/index` | Start a new crawl |
| GET | `/search?query=...` | Search the index |
| GET | `/status` | Global system overview |
| GET | `/status/{crawler_id}` | Single crawler detail + logs |
| POST | `/pause/{crawler_id}` | Pause a running crawler |
| POST | `/resume/{crawler_id}` | Resume a paused/stopped crawler |
| POST | `/stop/{crawler_id}` | Stop a crawler |

### Example: Start a crawl via curl

```bash
curl -X POST http://localhost:8000/index \
  -H "Content-Type: application/json" \
  -d '{"origin": "https://example.com", "k": 2, "max_pages": 100}'
```

### Example: Search via curl

```bash
curl "http://localhost:8000/search?query=python+tutorial&limit=10"
```

## Project Structure

```
Google-in-a-Day-Project/
  app.py                    # FastAPI entry point
  requirements.txt          # Python dependencies
  crawler/
    __init__.py
    crawler_engine.py       # Worker pool, BFS, back-pressure, rate limiting
    html_parser.py          # HTML text + link extraction (stdlib only)
  search/
    __init__.py
    search_engine.py        # Query normalization, scoring, pagination
  storage/
    __init__.py
    index_store.py          # Thread-safe in-memory inverted index + disk flush
    state_store.py          # Visited URLs, queue snapshots, crawler metadata
  static/
    index.html              # Single-page dashboard
    css/style.css           # Dark-themed responsive styles
    js/app.js               # Vanilla JS frontend logic
  data/                     # Auto-created at runtime (gitignored)
    visited_urls.data       # Global visited URL log
    crawlers/               # Per-crawler JSON state, queue, logs
    storage/                # Letter-sharded inverted index files
```

## Architecture

- **Concurrency:** Each crawl job spawns a coordinator thread that manages N worker threads pulling from a bounded `queue.Queue`. Workers fetch pages via `urllib.request`, parse HTML with `html.parser`, and feed results into a shared in-memory inverted index.
- **Back-pressure:** The queue has a configurable `maxsize`. When full, link discovery blocks (with timeout), and the event is logged. A shared rate limiter enforces a global requests-per-second cap across all workers.
- **Storage:** The inverted index lives in memory (for fast search) and is periodically flushed to letter-sharded `.data` files on disk. Visited URLs are appended to a global file after each fetch.
- **Resumability:** On stop/pause, the queue and crawler state are persisted. On resume, they are reloaded so the crawl continues without re-fetching visited pages.

## Deactivating the Virtual Environment

When you are done, deactivate the virtual environment:

```bash
deactivate
```
