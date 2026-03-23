"""FastAPI entry point: wires up /index, /search, /status endpoints.

Run with:  uvicorn app:app --reload --port 8000
"""

import threading
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from crawler.crawler_engine import CrawlerJob
from search.search_engine import SearchEngine
from storage.index_store import IndexStore
from storage.state_store import StateStore

# ---------------------------------------------------------------------------
# Shared singletons
# ---------------------------------------------------------------------------
index_store = IndexStore()
state_store = StateStore()
search_engine = SearchEngine(index_store)

# crawler_id -> CrawlerJob (in-memory registry of active/recent jobs)
active_crawlers: dict[str, CrawlerJob] = {}
crawlers_lock = threading.Lock()

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="Google-in-a-Day", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class IndexRequest(BaseModel):
    origin: str
    k: int = Field(ge=1, le=1000)
    max_workers: int = Field(default=4, ge=1, le=32)
    hit_rate: float = Field(default=2.0, ge=0.1, le=100.0)
    max_queue_size: int = Field(default=10000, ge=100, le=100000)
    max_pages: int = Field(default=500, ge=0, le=50000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _generate_crawler_id() -> str:
    epoch = int(time.time())
    tid = threading.get_ident()
    return f"{epoch}_{tid}"


# ---------------------------------------------------------------------------
# POST /index  — start a new crawl
# ---------------------------------------------------------------------------
@app.post("/index", status_code=201)
def start_index(req: IndexRequest) -> dict[str, Any]:
    if not _is_valid_url(req.origin):
        raise HTTPException(status_code=400, detail="Invalid origin URL")

    crawler_id = _generate_crawler_id()
    job = CrawlerJob(
        crawler_id=crawler_id,
        origin=req.origin,
        max_depth=req.k,
        index_store=index_store,
        state_store=state_store,
        max_workers=req.max_workers,
        hit_rate=req.hit_rate,
        max_queue_size=req.max_queue_size,
        max_pages=req.max_pages,
    )

    with crawlers_lock:
        active_crawlers[crawler_id] = job

    job.start()
    return {
        "crawler_id": crawler_id,
        "status": "started",
        "origin": req.origin,
        "k": req.k,
    }


# ---------------------------------------------------------------------------
# GET /search  — query the index
# ---------------------------------------------------------------------------
@app.get("/search")
def search(
    query: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return search_engine.search(query=query, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# GET /status  — global overview
# ---------------------------------------------------------------------------
@app.get("/status")
def global_status() -> dict[str, Any]:
    crawlers_info: list[dict[str, Any]] = []
    with crawlers_lock:
        for job in active_crawlers.values():
            crawlers_info.append(job.get_status())

    on_disk = state_store.list_crawler_ids()
    in_memory_ids = {c["crawler_id"] for c in crawlers_info}
    for cid in on_disk:
        if cid not in in_memory_ids:
            meta = state_store.load_crawler_meta(cid)
            if meta:
                crawlers_info.append(meta)

    crawlers_info.sort(key=lambda c: c.get("created_at", ""), reverse=True)

    return {
        "crawlers": crawlers_info,
        "total_indexed_pages": sum(
            c.get("pages_crawled", 0) for c in crawlers_info
        ),
        "total_indexed_words": index_store.unique_words,
    }


# ---------------------------------------------------------------------------
# GET /status/{crawler_id}  — single crawler detail
# ---------------------------------------------------------------------------
@app.get("/status/{crawler_id}")
def crawler_status(crawler_id: str) -> dict[str, Any]:
    with crawlers_lock:
        job = active_crawlers.get(crawler_id)

    if job:
        info = job.get_status()
    else:
        info = state_store.load_crawler_meta(crawler_id)
        if not info:
            raise HTTPException(status_code=404, detail="Crawler not found")

    info["logs"] = state_store.read_logs(crawler_id, tail=50)
    return info


# ---------------------------------------------------------------------------
# POST /stop/{crawler_id}
# ---------------------------------------------------------------------------
@app.post("/stop/{crawler_id}")
def stop_crawler(crawler_id: str) -> dict[str, str]:
    with crawlers_lock:
        job = active_crawlers.get(crawler_id)
    if not job:
        raise HTTPException(status_code=404, detail="Crawler not found")
    job.stop()
    return {"crawler_id": crawler_id, "status": "stopped"}


# ---------------------------------------------------------------------------
# POST /pause/{crawler_id}
# ---------------------------------------------------------------------------
@app.post("/pause/{crawler_id}")
def pause_crawler(crawler_id: str) -> dict[str, str]:
    with crawlers_lock:
        job = active_crawlers.get(crawler_id)
    if not job:
        raise HTTPException(status_code=404, detail="Crawler not found")
    job.pause()
    return {"crawler_id": crawler_id, "status": "paused"}


# ---------------------------------------------------------------------------
# POST /resume/{crawler_id}
# ---------------------------------------------------------------------------
@app.post("/resume/{crawler_id}")
def resume_crawler(crawler_id: str) -> dict[str, str]:
    with crawlers_lock:
        job = active_crawlers.get(crawler_id)

    if job and job.is_alive:
        job.resume()
        return {"crawler_id": crawler_id, "status": "resumed"}

    meta = state_store.load_crawler_meta(crawler_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Crawler not found")

    queue_items = state_store.load_queue(crawler_id)
    if not queue_items:
        raise HTTPException(status_code=400, detail="No queue data to resume from")

    index_store.load_from_disk()

    new_job = CrawlerJob(
        crawler_id=crawler_id,
        origin=meta["origin"],
        max_depth=meta["max_depth"],
        index_store=index_store,
        state_store=state_store,
        max_workers=meta.get("max_workers", 4),
        hit_rate=meta.get("hit_rate", 2.0),
        max_queue_size=meta.get("max_queue_size", 10000),
        max_pages=meta.get("max_pages", 500),
    )

    with crawlers_lock:
        active_crawlers[crawler_id] = new_job

    new_job.start(resume_items=queue_items)
    return {"crawler_id": crawler_id, "status": "resumed_from_disk"}


# ---------------------------------------------------------------------------
# Static files — serve the dashboard UI
# ---------------------------------------------------------------------------
import os

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/")
    def serve_dashboard() -> FileResponse:
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
