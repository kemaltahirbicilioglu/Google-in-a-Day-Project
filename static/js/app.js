/* Google-in-a-Day  —  Frontend JS (vanilla, no frameworks) */

const API = "";  // same origin

// ── Tab Switching ──────────────────────────────────────────
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(s => s.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");

    if (btn.dataset.tab === "status") refreshStatus();
    if (btn.dataset.tab === "crawler") refreshCrawlerList();
  });
});

// ── Crawler Form ───────────────────────────────────────────
document.getElementById("crawl-form").addEventListener("submit", async e => {
  e.preventDefault();
  const msgEl = document.getElementById("crawl-result");

  const body = {
    origin: document.getElementById("origin").value.trim(),
    k: parseInt(document.getElementById("depth").value),
    max_workers: parseInt(document.getElementById("max-workers").value),
    hit_rate: parseFloat(document.getElementById("hit-rate").value),
    max_queue_size: parseInt(document.getElementById("max-queue").value),
    max_pages: parseInt(document.getElementById("max-pages").value),
  };

  try {
    const res = await fetch(`${API}/index`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");

    msgEl.textContent = `Crawl started! ID: ${data.crawler_id}`;
    msgEl.className = "msg success";
    msgEl.hidden = false;
    refreshCrawlerList();
  } catch (err) {
    msgEl.textContent = `Error: ${err.message}`;
    msgEl.className = "msg error";
    msgEl.hidden = false;
  }
});

// ── Crawler History List ───────────────────────────────────
async function refreshCrawlerList() {
  try {
    const data = await (await fetch(`${API}/status`)).json();
    const el = document.getElementById("crawler-list");
    if (!data.crawlers.length) {
      el.innerHTML = '<p class="muted">No crawlers yet.</p>';
      return;
    }
    el.innerHTML = data.crawlers.map(c => `
      <div class="crawler-item">
        <span class="origin" title="${esc(c.origin)}">${esc(c.origin)}</span>
        <span class="badge badge-${c.status}">${c.status}</span>
        <span style="margin-left:.5rem;color:var(--muted);font-size:.8rem">
          ${c.pages_crawled} pg | d=${c.max_depth}
        </span>
      </div>
    `).join("");
  } catch { /* silent */ }
}

// ── Status Tab ─────────────────────────────────────────────
let statusInterval = null;

async function refreshStatus() {
  try {
    const data = await (await fetch(`${API}/status`)).json();

    document.getElementById("stat-pages").textContent = data.total_indexed_pages;
    document.getElementById("stat-words").textContent = data.total_indexed_words;
    const active = data.crawlers.filter(c => c.status === "running" || c.status === "paused").length;
    document.getElementById("stat-crawlers").textContent = active;

    const container = document.getElementById("status-details");
    if (!data.crawlers.length) {
      container.innerHTML = '<div class="card"><p class="muted">No crawlers to display.</p></div>';
      return;
    }

    const details = await Promise.all(
      data.crawlers.map(c =>
        fetch(`${API}/status/${c.crawler_id}`).then(r => r.json()).catch(() => c)
      )
    );

    container.innerHTML = details.map(c => {
      const qPct = c.max_queue_size ? Math.round((c.queue_depth / c.max_queue_size) * 100) : 0;
      const pgPct = c.max_pages ? Math.round((c.pages_crawled / c.max_pages) * 100) : 0;
      const logs = (c.logs || []).slice(-10).join("\n");
      const isActive = c.status === "running" || c.status === "paused";

      return `
        <div class="status-card">
          <h3>
            <span class="badge badge-${c.status}">${c.status}</span>
            <span style="font-weight:400;color:var(--muted);font-size:.82rem">${esc(c.origin)}</span>
          </h3>

          <div style="font-size:.8rem;color:var(--muted);margin-bottom:.25rem">
            Pages: ${c.pages_crawled}${c.max_pages ? " / " + c.max_pages : ""} (${pgPct}%)
          </div>
          <div class="progress-bar"><div class="progress-fill" style="width:${pgPct}%"></div></div>

          <div style="font-size:.8rem;color:var(--muted);margin-bottom:.25rem">
            Queue: ${c.queue_depth} / ${c.max_queue_size} (${qPct}%)
          </div>
          <div class="progress-bar"><div class="progress-fill" style="width:${qPct}%"></div></div>

          <div class="status-meta">
            <div><small>Crawler ID</small>${c.crawler_id}</div>
            <div><small>Depth</small>${c.max_depth}</div>
            <div><small>Workers</small>${c.max_workers}</div>
            <div><small>Hit Rate</small>${c.hit_rate} req/s</div>
            <div><small>Back-pressure</small>${c.backpressure_events} events</div>
            <div><small>Created</small>${formatTime(c.created_at)}</div>
          </div>

          ${logs ? `<div class="log-box">${esc(logs)}</div>` : ""}

          ${isActive ? `
            <div class="controls">
              ${c.status === "running"
                ? `<button class="btn-small" onclick="ctrlCrawler('${c.crawler_id}','pause')">Pause</button>`
                : `<button class="btn-small" onclick="ctrlCrawler('${c.crawler_id}','resume')">Resume</button>`
              }
              <button class="btn-small btn-danger" onclick="ctrlCrawler('${c.crawler_id}','stop')">Stop</button>
            </div>
          ` : c.status === "stopped" ? `
            <div class="controls">
              <button class="btn-small" onclick="ctrlCrawler('${c.crawler_id}','resume')">Resume from Disk</button>
            </div>
          ` : ""}
        </div>
      `;
    }).join("");
  } catch { /* silent */ }
}

async function ctrlCrawler(id, action) {
  try {
    await fetch(`${API}/${action}/${id}`, { method: "POST" });
    refreshStatus();
  } catch { /* silent */ }
}

// Auto-refresh status every 2s while the tab is active
function startStatusPolling() {
  if (statusInterval) clearInterval(statusInterval);
  statusInterval = setInterval(() => {
    if (document.querySelector('.tab[data-tab="status"]').classList.contains("active")) {
      refreshStatus();
    }
  }, 2000);
}
startStatusPolling();

// ── Search ─────────────────────────────────────────────────
let searchOffset = 0;
const SEARCH_LIMIT = 20;

document.getElementById("search-form").addEventListener("submit", async e => {
  e.preventDefault();
  searchOffset = 0;
  doSearch();
});

async function doSearch() {
  const query = document.getElementById("search-query").value.trim();
  if (!query) return;

  try {
    const url = `${API}/search?query=${encodeURIComponent(query)}&limit=${SEARCH_LIMIT}&offset=${searchOffset}`;
    const data = await (await fetch(url)).json();

    const container = document.getElementById("search-results");
    container.hidden = false;
    document.getElementById("result-count").textContent = `${data.total} total`;

    const tbody = document.querySelector("#results-table tbody");
    if (!data.results.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="muted">No results found.</td></tr>';
    } else {
      tbody.innerHTML = data.results.map(r => `
        <tr>
          <td><a href="${esc(r.relevant_url)}" target="_blank">${esc(r.relevant_url)}</a></td>
          <td style="color:var(--muted)">${esc(r.origin_url)}</td>
          <td>${r.depth}</td>
          <td>${r.score}</td>
        </tr>
      `).join("");
    }

    // Pagination
    const pages = Math.ceil(data.total / SEARCH_LIMIT);
    const current = Math.floor(searchOffset / SEARCH_LIMIT);
    const pag = document.getElementById("pagination");
    if (pages <= 1) {
      pag.innerHTML = "";
    } else {
      let btns = "";
      for (let i = 0; i < Math.min(pages, 10); i++) {
        const cls = i === current ? "btn-primary" : "btn-small";
        btns += `<button class="${cls}" onclick="goPage(${i})">${i + 1}</button>`;
      }
      pag.innerHTML = btns;
    }
  } catch { /* silent */ }
}

function goPage(page) {
  searchOffset = page * SEARCH_LIMIT;
  doSearch();
}

// ── Helpers ────────────────────────────────────────────────
function esc(str) {
  if (!str) return "";
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

function formatTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch { return iso; }
}

// Initial load
refreshCrawlerList();
