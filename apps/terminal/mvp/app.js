"use strict";

/* ── helpers ─────────────────────────────────────────────── */
let toastTimer = null;
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 2600);
}

const TERMINAL_ACCESS_PREFIX = (() => {
  const pathname = window.location.pathname.replace(/\/+$/, "");
  return pathname === "/" ? "" : pathname;
})();

function terminalApiPath(path) {
  const normalized = String(path || "").startsWith("/")
    ? String(path)
    : `/${String(path || "")}`;
  return `${TERMINAL_ACCESS_PREFIX}${normalized}`;
}

async function api(path, opts) {
  const mode = document.querySelector("[data-unavailable-api-prefixes]");
  const unavailable = mode ? JSON.parse(mode.dataset.unavailableApiPrefixes) : [];
  const pathname = String(path).split("?")[0];
  if (unavailable.some((prefix) => pathname === prefix || pathname.startsWith(prefix + "/"))) {
    throw new Error("Private workspace features are unavailable in this public demo.");
  }
  const res = await fetch(terminalApiPath(path), opts);
  if (!res.ok) {
    let msg = res.status + " " + path;
    try { const j = await res.json(); if (j && j.error) msg = j.error; } catch (e) {}
    throw new Error(msg);
  }
  return res.json();
}

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));

function fmtDate(v) {
  if (!v) return "";
  const s = String(v).slice(0, 10);
  return s === "1970-01-01" ? "" : s;
}

function statusChip(status) {
  const obs = String(status || "").toUpperCase() === "OBSERVED";
  return `<span class="chip ${obs ? "obs" : "unk"}">${esc(status || "UNKNOWN")}</span>`;
}

function money(p) {
  if (p == null) return "";
  const n = Number(p);
  return Number.isFinite(n) ? (n % 1 === 0 ? n.toLocaleString() : n.toFixed(2)) : "";
}

/* ── router ──────────────────────────────────────────────── */
const view = document.getElementById("view");

function setNav(active) {
  document.querySelectorAll(".nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.nav === active);
  });
}

let routeVersion = 0;
function route() {
  routeVersion += 1;
  const raw = location.hash.replace(/^#\/?/, "").split("?")[0];
  const parts = raw.split("/").map(decodeURIComponent).filter((p) => p.length);
  if (!parts.length) { renderHome(); return; }
  const [head, ...rest] = parts;
  if (head === "artist" && rest.length) renderArtist(rest.join("/"));
  else if (head === "search" && rest.length) doSearch(rest.join(" "));
  else if (head === "market" && rest.length) renderMarket(rest.join("/"));
  else if (head === "markets") renderMarkets();
  else if (head === "underwrite") renderUnderwrite();
  else if (head === "backtest" && rest.length) renderPIT(rest.join("/").replace(/^show\//, ""));
  else if (head === "backtest") renderBacktest();
  else if (head === "portfolio") renderPortfolio();
  else if (head === "monitor") renderMonitor();
  else if (head === "compare") renderCompare();
  else if (head === "shortlist") renderShortlist();
  else if (head === "demo") renderDemo();
  else if (head === "home") renderHome();
  else renderHome();
}

/* ── home ────────────────────────────────────────────────── */
async function renderHome() {
  const renderVersion = routeVersion;
  setNav("home");
  view.innerHTML = `<h1>Talent Buyer Terminal</h1>
    <p class="muted">Real Festival Bloomberg evidence — search an artist, inspect a market, or start the guided demo.</p>
    <div style="height:12px"></div>
    <div class="grid cols2">
      <div class="panel hero">
        <h3>What is happening now?</h3>
        <div id="nowBox" class="empty">loading…</div>
      </div>
      <div class="panel">
        <h3>What am I evaluating?</h3>
        <div id="slBox" class="empty">loading…</div>
      </div>
    </div>
    <div style="height:14px"></div>
    <div class="grid cols2">
      <div class="panel">
        <h3>Since your last look <a class="small" href="#/monitor">monitor →</a></h3>
        <div id="monStrip" class="empty">loading…</div>
      </div>
      <div class="panel">
        <h3>Start an underwrite</h3>
        <p class="small muted">Artist + market + date → full pre-offer buyer brief.</p>
        <div class="form-row"><label>Artist</label><input id="uwArtist" type="search" placeholder="Search artist…"><div id="uwArtistHits"></div></div>
        <div class="form-row"><label>Market</label><input id="uwMarket" type="text" placeholder="e.g. chicago-il or Chicago"></div>
        <div class="form-row"><label>Date</label><input id="uwDate" type="date"></div>
        <button class="btn primary" id="uwGo">Build buyer brief →</button>
      </div>
    </div>
    <div style="height:14px"></div>
    <div class="panel">
      <h3>Start here — artists with the most evidence <a class="small" href="#/demo">all demo artists →</a></h3>
      <div id="demoStrip" class="demo-grid"><div class="empty">loading…</div></div>
      <p class="small muted">Each card shows which evidence families are observed. Click to open the Artist Security page.</p>
      <div style="height:6px"></div>
      <a class="btn primary" href="#/demo">START DEMO</a>
    </div>
    <div style="height:14px"></div>
    <div class="panel"><h3>Browse markets by density <a class="small" href="#/markets">all markets →</a></h3><div id="marketStrip" class="empty">loading…</div></div>
    <div style="height:14px"></div>
    <div id="freshLine" class="small muted"></div>`;

  // Freshness — one compact line, not a wall of database counts.
  try {
    const cov = await api("/api/coverage");
    if (renderVersion !== routeVersion) return;
    const c = cov.counts || {};
    document.getElementById("freshLine").innerHTML =
      `<span class="badge">${esc(cov.generation || "")}</span> ` +
      `${(c.artists ?? 0).toLocaleString()} artists · ${(c.event_history ?? 0).toLocaleString()} live events · ` +
      `${(c.artist_peers ?? 0).toLocaleString()} audience peer edges · ${(c.future_events ?? 0).toLocaleString()} forward events` +
      ` · built ${esc(String(cov.built_at || "").slice(0, 10))} · ${esc(cov.validation_status || "")}` +
      ` — <span class="muted">UNKNOWN ≠ 0 · audience sample ≠ total fans</span>`;
  } catch (e) { if (renderVersion !== routeVersion) return; document.getElementById("freshLine").innerHTML = `<span class="empty">${esc(e.message)}</span>`; }

  // Now strip: upcoming forward shows + recent live activity.
  try {
    const n = await api("/api/now");
    if (renderVersion !== routeVersion) return;
    const box = document.getElementById("nowBox");
    const up = (n.upcoming || []).map((r) => `
      <div class="now-row" data-k="${esc(r.artist_key)}">
        <b>${esc(r.artist_name)}</b> · <span class="muted">${esc(fmtDate(r.event_date))}${r.venue_city ? " · " + esc(r.venue_city) : ""}</span>
        <span class="chip ${r.event_status === "onsale" ? "obs" : "unk"}">${esc(r.event_status || "listed")}</span>
      </div>`).join("");
    const rec = (n.recent || []).map((r) => `
      <div class="now-row" data-k="${esc(r.artist_key)}">
        <b>${esc(r.artist_name)}</b> · <span class="muted">${esc(fmtDate(r.event_date))} · ${esc(r.event_name || "").slice(0, 46)}</span>
      </div>`).join("");
    box.innerHTML =
      (up ? `<div class="small" style="margin-bottom:6px">Upcoming shows (provider-listed, not sales)</div>${up}` : "") +
      (rec ? `<div class="small" style="margin:10px 0 6px">Recent observed live activity</div>${rec}` : "");
    if (!up && !rec) box.innerHTML = `<div class="empty">No forward or recent activity in this generation.</div>`;
    box.querySelectorAll(".now-row").forEach((el) => {
      el.onclick = () => location.hash = "#/artist/" + encodeURIComponent(el.dataset.k);
    });
  } catch (e) { if (renderVersion !== routeVersion) return; document.getElementById("nowBox").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }

  // Shortlist preview: what am I currently evaluating?
  try {
    const sl = await api("/api/shortlist");
    if (renderVersion !== routeVersion) return;
    const box = document.getElementById("slBox");
    if (!sl.length) {
      box.innerHTML = `<div class="empty">Nothing yet. Open an artist and hit <b>＋ Shortlist</b>, or <a href="#/shortlist">start a project</a>.</div>`;
    } else {
      box.innerHTML = sl.slice(0, 5).map((it) => `
        <div class="now-row" ${it.artist_key ? `data-k="${esc(it.artist_key)}"` : ""}>
          <b>${esc(it.name)}</b>${it.market ? ` · <span class="muted">${esc(it.market)}</span>` : ""}
          ${it.event_date ? `<span class="muted"> · ${esc(fmtDate(it.event_date))}</span>` : ""}
        </div>`).join("") +
        `<div style="margin-top:8px"><a class="small" href="#/shortlist">Open shortlist →</a></div>`;
      box.querySelectorAll(".now-row[data-k]").forEach((el) => {
        el.onclick = () => location.hash = "#/artist/" + encodeURIComponent(el.dataset.k);
      });
    }
  } catch (e) { if (renderVersion !== routeVersion) return; document.getElementById("slBox").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }

  // Since-your-last-look strip from the monitor baselines.
  try {
    const mon = await api("/api/monitor");
    if (renderVersion !== routeVersion) return;
    const box = document.getElementById("monStrip");
    const rows = (mon.artists || []).filter((a) => a.changes && a.changes.length);
    if (!rows.length) {
      const watched = (mon.artists || []).length;
      box.innerHTML = watched
        ? `<div class="empty">${watched} watched artist${watched > 1 ? "s" : ""} — no changes since the last look at this generation.</div>`
        : `<div class="empty">Shortlist artists are watched here. Keep an eye on what changed since you last looked.</div>`;
    } else {
      box.innerHTML = rows.map((a) => `
        <div class="now-row" data-k="${esc(a.artist_key)}">
          <b>${esc(a.artist_name)}</b>
          <span class="small">${a.changes.map((c) =>
            `<span class="chip obs">${esc(c.detail)}</span>`).join(" ")}</span>
        </div>`).join("");
      box.querySelectorAll(".now-row[data-k]").forEach((el) => {
        el.onclick = () => location.hash = "#/monitor";
      });
    }
  } catch (e) { if (renderVersion !== routeVersion) return; document.getElementById("monStrip").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }

  // Underwrite launcher wiring.
  const uwArtist = document.getElementById("uwArtist");
  let uwKey = "";
  uwArtist.addEventListener("input", async () => {
    const q = uwArtist.value.trim();
    const hits = document.getElementById("uwArtistHits");
    if (q.length < 2) { hits.innerHTML = ""; return; }
    try {
      const res = await api("/api/search?q=" + encodeURIComponent(q) + "&limit=6");
    if (renderVersion !== routeVersion) return;
      hits.innerHTML = res.map((h) =>
        `<div class="result" data-k="${esc(h.entity_id)}" data-name="${esc(h.name)}"><b>${esc(h.name)}</b></div>`
      ).join("");
      hits.querySelectorAll(".result").forEach((el) => {
        el.onclick = () => { uwKey = el.dataset.k; uwArtist.value = el.dataset.name; hits.innerHTML = ""; };
      });
    } catch (e) { if (renderVersion !== routeVersion) return; hits.innerHTML = `<span class="empty">${esc(e.message)}</span>`; }
  });
  document.getElementById("uwGo").onclick = () => {
    if (!uwKey.trim()) { toast("Pick an artist from search first."); return; }
    const m = document.getElementById("uwMarket").value.trim();
    const d = document.getElementById("uwDate").value;
    location.hash = "#/underwrite?a=" + encodeURIComponent(uwKey) + (m ? "&m=" + encodeURIComponent(m) : "") + (d ? "&d=" + encodeURIComponent(d) : "");
  };

  try {
    const demo = await api("/api/demo");
    if (renderVersion !== routeVersion) return;
    const strip = document.getElementById("demoStrip");
    if (!demo.length) { strip.innerHTML = `<div class="empty">No demo artists yet.</div>`; return; }
    strip.innerHTML = demo.slice(0, 6).map((d) => demoCard(d)).join("");
    strip.querySelectorAll(".demo-card").forEach((el, i) => {
      el.onclick = () => { location.hash = "#/artist/" + encodeURIComponent(demo[i].artist_key); };
    });
  } catch (e) { if (renderVersion !== routeVersion) return; document.getElementById("demoStrip").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  try {
    const m = await api("/api/markets?limit=8");
    if (renderVersion !== routeVersion) return;
    const strip = document.getElementById("marketStrip");
    if (!m.items.length) { strip.innerHTML = `<div class="empty">No market links materialized.</div>`; return; }
    strip.innerHTML = m.items.map((r) => `
      <div class="market-row" data-m="${esc(r.market_key)}">
        <b>${esc(r.pretty)}</b><span class="muted">${r.artist_count} artists · ${r.total_shows} observed shows</span>
      </div>`).join("");
    strip.querySelectorAll(".market-row").forEach((el) => {
      el.onclick = () => location.hash = "#/market/" + encodeURIComponent(el.dataset.m);
    });
  } catch (e) { if (renderVersion !== routeVersion) return; document.getElementById("marketStrip").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

function demoCard(d) {
  const max = Math.max(1, d.completeness || 1);
  const parts = [
    ["Identity", 1], ["Markets", +(d.market_count > 0)], ["Live", +(d.historical_event_count > 0)],
    ["Festivals", +(d.festival_appearance_count > 0)], ["Attention", +(d.attention_source_count > 0)],
    ["Peers", +(d.peer_count > 0)], ["Forward", +(d.future_event_count > 0)],
  ];
  return `<div class="demo-card">
    <div class="name">${esc(d.name)}</div>
    <div class="sub">${esc(d.tier || "")} · ${d.completeness}/7 evidence families</div>
    <div class="bars">${parts.map(([label, on]) =>
      `<div class="bar"><i style="width:${on ? 100 : 6}%;${on ? "" : "background:#3b4550"}" title="${label}"></i></div>`).join("")}</div>
  </div>`;
}

/* ── search ──────────────────────────────────────────────── */
async function doSearch(q) {
  if (!q.trim()) return;
  view.innerHTML = `<h1>Search: ${esc(q)}</h1><div class="empty">searching…</div>`;
  try {
    const hits = await api("/api/search?q=" + encodeURIComponent(q) + "&limit=25");
    if (!hits.length) {
      view.innerHTML = `<h1>Search: ${esc(q)}</h1><div class="empty">No artist matched. Try a different spelling or a demo artist.</div>`;
      return;
    }
    view.innerHTML = `<h1>Search: ${esc(q)} <span class="badge">${hits.length} hits</span></h1>
      <div class="results">
        ${hits.map((h) => `
          <div class="result" data-k="${esc(h.entity_id)}" data-name="${esc(h.name)}">
            <div><b>${esc(h.name)}</b>
              ${h.tier ? `<span class="badge">${esc(h.tier)}</span>` : ""}</div>
            <div class="meta">${esc(h.mbid ? h.mbid.slice(0, 8) : "")} · ${esc(h.matched_term_type || "canonical name")}</div>
            <button class="btn small" data-sl>＋ Shortlist</button>
          </div>`).join("")}
      </div>`;
    view.querySelectorAll(".result").forEach((el) => {
      el.onclick = (ev) => {
        if (ev.target.closest("[data-sl]")) return;
        location.hash = "#/artist/" + encodeURIComponent(el.dataset.k);
      };
    });
    view.querySelectorAll(".result [data-sl]").forEach((el) => {
      el.onclick = async () => {
        const row = el.closest(".result");
        try {
          await api("/api/shortlist", { method: "POST", body: JSON.stringify({ name: row.dataset.name, artist_key: row.dataset.k, notes: "added from search" }) });
          toast("Added " + row.dataset.name + " to shortlist.");
        } catch (e) { toast(e.message); }
      };
    });
  } catch (e) { view.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* ── artist ──────────────────────────────────────────────── */
let compareDraft = [];

async function renderArtist(key) {
  setNav("");
  view.innerHTML = `<h1>Artist</h1><div class="empty">loading ${esc(key)}…</div>`;
  let p;
  try { p = await api("/api/artist-security/" + encodeURIComponent(key)); }
  catch (e) { view.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const a = p.artist || {};
  const qf = p.quick_facts || {};
  const cov = a.coverage_state || {};
  compareDraft = compareDraft.filter((k) => k !== a.artist_key);

  view.innerHTML = `
  <div class="artist-header">
    <h1>${esc(a.name)}</h1>
    ${a.tier ? `<span class="tier">${esc(a.tier)}</span>` : ""}
    ${statusChip(cov.identity)}
    <button class="btn" id="shortlistBtn">＋ Shortlist</button>
    <button class="btn" id="compareBtn">Compare with…</button>
    <button class="btn primary" id="underwriteBtn">Underwrite…</button>
  </div>
  <p class="muted small">${esc(a.musicbrainz_id || "")}${a.artist_type ? " · " + esc(a.artist_type) : ""}${a.area ? " · " + esc(a.area) : ""}${a.primary_genre ? " · " + esc(a.primary_genre) : ""}</p>
  <p class="small muted">${esc(a.evidence_coverage || "")} · ${esc(a.freshness || "")}</p>
  <div style="height:10px"></div>
  <div class="quickfacts">
    <div class="fact"><div class="n">${qf.historical_events ?? "—"}</div><div class="l">Live events</div></div>
    <div class="fact"><div class="n">${qf.festival_appearances ?? "—"}</div><div class="l">Festival appearances</div></div>
    <div class="fact"><div class="n">${qf.markets ?? "—"}</div><div class="l">Markets</div></div>
    <div class="fact"><div class="n">${qf.venues_played ?? "—"}</div><div class="l">Venues played</div></div>
    <div class="fact"><div class="n">${qf.future_events ?? "—"}</div><div class="l">Forward events</div></div>
    <div class="fact"><div class="n">${qf.current_ticket_ranges ?? "—"}</div><div class="l">Advertised ticket ranges</div></div>
    <div class="fact"><div class="n">${qf.audience_peers ?? "—"}</div><div class="l">Audience peers</div></div>
  </div>
  <div style="height:14px"></div>
  <div class="grid cols2" id="attentionPanels"></div>
  <div style="height:14px"></div>
  <div class="panel"><h3>Artist factor tape ${statusChip((p.factor_tape && p.factor_tape.status) || "UNKNOWN")}</h3><div id="tapeBox"></div></div>
  <div style="height:14px"></div>
  <div class="grid cols2">
    <div class="panel"><h3>Sentiment ${statusChip((p.sentiment && p.sentiment.status) || "UNKNOWN")}</h3><div id="sentimentBox"></div></div>
    <div class="panel"><h3>Provider data rails</h3><div id="providersBox"></div></div>
  </div>
  <div style="height:14px"></div>
  <div class="grid cols2">
    <div class="panel"><h3>Audience peers ${statusChip(p.peers.status)}</h3><div id="peersBox"></div><p class="note">${esc(p.peers.note || "")}</p></div>
    <div class="panel"><h3>Alternatives ${statusChip(p.alternatives.status)}</h3><div id="altsBox" data-subject="${esc(a.artist_key)}"></div><p class="note">${esc(p.alternatives.note || "")}</p></div>
  </div>
  <div style="height:14px"></div>
  <div class="grid cols2">
    <div class="panel"><h3>Market footprint ${statusChip(p.markets.status)}</h3><div id="marketsBox"></div></div>
    <div class="panel"><h3>Recent live history ${statusChip(p.history.status)}</h3><div id="historyBox"></div></div>
  </div>
  <div style="height:14px"></div>
  <div class="grid cols2">
    <div class="panel"><h3>Festival history ${statusChip(p.festivals.status)}</h3><div id="festBox"></div></div>
    <div class="panel"><h3>Forward events ${statusChip(p.future.status)}</h3><div id="futureBox"></div></div>
  </div>
  <div style="height:14px"></div>
  <div class="panel"><h3>PUBLIC TICKET MARKET ${statusChip((p.public_ticket_market && p.public_ticket_market.status) || "UNKNOWN")}</h3><div id="ticketMarketBox"></div><p class="note">${esc((p.public_ticket_market && p.public_ticket_market.note) || "")}</p></div>
  <div style="height:14px"></div>
  <div class="panel"><h3>Evidence summary</h3><div id="evidenceBox"></div></div>`;

  document.getElementById("shortlistBtn").onclick = () => quickShortlist(a, p);
  document.getElementById("underwriteBtn").onclick = () => {
    const topMarket = (p.markets && p.markets.items && p.markets.items[0]);
    const m = topMarket ? (topMarket.market || topMarket.market_name || topMarket.market_key || "") : "";
    location.hash = "#/underwrite?a=" + encodeURIComponent(a.artist_key) + (m ? "&m=" + encodeURIComponent(m) : "");
  };
  document.getElementById("compareBtn").onclick = () => {
    compareDraft = [a.artist_key];
    location.hash = "#/compare";
  };

  renderAttention(p.attention || {});
  renderTape((p.factor_tape || {}), (p.what_changed || []));
  renderSentiment(p.sentiment || {});
  renderProviders(p.provider_readiness || {});
  renderPeers(p.peers);
  renderAlts(p.alternatives);
  renderArtistMarkets(p.markets);
  renderHistory(p.history);
  renderFestivals(p.festivals);
  renderFuture(p.future);
  renderPublicTicketMarket(p.public_ticket_market || {});
  renderEvidence(p.evidence);

  // Alternative provider link events: clicking an alt fills compare.
  document.querySelectorAll("[data-compare]").forEach((el) => {
    el.onclick = (ev) => {
      ev.stopPropagation();
      compareDraft = [a.artist_key, el.dataset.compare];
      location.hash = "#/compare";
    };
  });
}

function renderAttention(attention) {
  const box = document.getElementById("attentionPanels");
  if (!box) return;
  const order = ["listenbrainz", "wikimedia", "youtube"];
  box.innerHTML = order.map((src) => {
    const block = attention[src];
    if (!block || block.status !== "OBSERVED") {
      return `<div class="panel"><h3>${esc(src)} ${statusChip("UNKNOWN")}</h3><div class="empty">${esc((block && block.note) || "No observation.")}</div></div>`;
    }
    const latest = block.metrics && block.metrics[0];
    const series = (block.series || []).slice(0, 3);
    return `<div class="panel"><h3>${esc(src)} ${statusChip("OBSERVED")}</h3>
      <div class="small muted">latest ${esc(fmtDate(block.latest_observation))}${block.latest_knowledge_time ? " · knowledge " + esc(String(block.latest_knowledge_time).slice(0, 10)) : ""}</div>
      ${series.map((s) => {
        const vals = (s.points || []).map((pt) => Number(pt.v) || 0);
        const max = Math.max(...vals, 1);
        return `<div style="margin-top:10px"><div class="small">${esc(s.label)}</div>
          <div class="spark">${vals.slice(-40).map((v) => `<span style="height:${Math.max(8, (v / max) * 100)}%"></span>`).join("")}</div></div>`;
      }).join("")}
      <p class="note">${esc(block.note || "")}</p></div>`;
  }).join("");
}

function fmtDelta(d) {
  const n = Number(d);
  if (!Number.isFinite(n)) return "";
  return (n >= 0 ? "+" : "") + n.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function renderTape(tape, changes) {
  const box = document.getElementById("tapeBox");
  if (!box) return;
  const items = (tape.items || []);
  if (!items.length) {
    box.innerHTML = `<div class="empty">${esc(tape.note || "No temporal factor observations in this serving generation.")}</div>`;
    return;
  }
  const changed = (changes || []);
  const changeHtml = changed.length
    ? `<div style="margin-bottom:8px">${changed.slice(0, 12).map((c) => {
        if (c.comparability !== "COMPARABLE") {
          return `<span class="chip" style="margin:1px 6px 1px 0" title="${esc(c.comparability_reason || "Measurement context is incomplete or differs")}">${esc(c.factor_name)} · NOT_COMPARABLE</span>`;
        }
        const pct = c.delta_pct != null ? ` (${(Number(c.delta_pct) >= 0 ? "+" : "")}${Number(c.delta_pct).toFixed(1)}%)` : "";
        return `<span class="chip" style="margin:1px 6px 1px 0" title="${esc((c.period && (c.period.from + " → " + c.period.to)) || "")} · ${esc(c.source || "")} · ${esc(c.generation || "")}">${esc(c.factor_name)} ${fmtDelta(c.delta)}${pct}</span>`;
      }).join("")}</div><div class="small muted">what changed — deltas only from comparable observations</div>`
    : `<p class="note">No comparable-pair delta yet: UNKNOWN is distinct from zero and history is never reconstructed from a current snapshot.</p>`;
  const seriesHtml = (tape.series || []).map((s) => {
    const vals = (s.points || []).map((pt) => Number(pt.v) || 0);
    const max = Math.max(...vals, 1);
    return `<div style="margin-top:8px"><div class="small">${esc(s.label)} · <span class="muted">source ${esc(s.source || "")}${s.sample_size ? " · sample " + esc(s.sample_size) : ""}</span></div>
      <div class="spark">${vals.slice(-90).map((v) => `<span style="height:${Math.max(8, (v / max) * 100)}%"></span>`).join("")}</div>
      <div class="small muted">${esc((s.period && s.period.start) || "")} → ${esc((s.period && s.period.end) || "")} · freshness ${esc(s.freshness || "")}</div></div>`;
  }).join("");
  box.innerHTML = `<p class="note">${esc(tape.note || "")}</p>
    ${changeHtml}
    <table><thead><tr><th>Factor</th><th>Platform</th><th>Value</th><th>Period</th><th>Source</th><th>Freshness</th></tr></thead><tbody>
    ${items.slice(0, 60).map((r) => `<tr>
      <td>${esc(r.factor_name)}</td><td>${esc(r.platform || r.source || "")}</td>
      <td>${r.value == null ? "UNKNOWN" : esc(money(r.value)) + " " + esc(r.unit || "")}</td>
      <td class="small muted">${esc(fmtDate(r.observation_time || r.as_of))}</td>
      <td class="small muted">${esc(r.source || r.source_system || "")}</td>
      <td class="small muted">${esc(fmtDate(r.freshness || r.retrieved_at))}</td></tr>`).join("")}
    </tbody></table>
    ${seriesHtml ? `<div style="margin-top:10px"><div class="small">Time series (≥2 observations)</div>${seriesHtml}</div>` : ""}`;
}

function renderSentiment(sentiment) {
  const box = document.getElementById("sentimentBox");
  if (!box) return;
  const items = (sentiment.items || []);
  if (!items.length) {
    box.innerHTML = `<div class="empty">${esc(sentiment.note || "No daily aggregate yet — raw identities are never served.")}</div>`;
    return;
  }
  const latest = items.slice(0, 5);
  box.innerHTML = `<p class="note">${esc(sentiment.note || "")}</p>
    <table><thead><tr><th>Day</th><th>Platform</th><th>Mean</th><th>+ / = / −</th><th>Analyzed</th><th>Model</th></tr></thead><tbody>
    ${latest.map((r) => `<tr><td>${esc(fmtDate(r.date))}</td><td>${esc(r.platform)}</td><td>${r.sentiment_mean == null ? "UNKNOWN" : Number(r.sentiment_mean).toFixed(3)}</td>
      <td class="small">${r.positive_share == null ? "—" : (Number(r.positive_share) * 100).toFixed(0) + " / " + (Number(r.neutral_share) * 100).toFixed(0) + " / " + (Number(r.negative_share) * 100).toFixed(0)}</td>
      <td class="small muted">${esc(r.analyzed_count)} / ${esc(r.mention_count)} mentions</td>
      <td class="small muted">${esc(r.model_name)}@${esc(r.model_version)}</td></tr>`).join("")}</tbody></table>`;
}

function renderProviders(providers) {
  const box = document.getElementById("providersBox");
  if (!box) return;
  const entries = Object.entries(providers || {});
  if (!entries.length) {
    box.innerHTML = `<div class="empty">No provider readiness published for this generation.</div>`;
    return;
  }
  box.innerHTML = entries.map(([name, r]) => `<div style="margin-bottom:8px">
    <div class="small"><b>${esc(name)}</b> ${statusChip(r.status)} ${r.historical_strategy ? `<span class="muted">· ${esc(r.historical_strategy)}</span>` : ""}</div>
    <div class="small muted">${esc(r.note || "")}</div></div>`).join("");
}

function renderPeers(peers) {
  const box = document.getElementById("peersBox");
  if (!box) return;
  if (!peers.items || !peers.items.length) {
    box.innerHTML = `<div class="empty">No audience peer evidence for this artist.</div>`;
    return;
  }
  box.innerHTML = `<table><thead><tr><th>Peer</th><th>Shared</th><th>Why related</th></tr></thead><tbody>
    ${peers.items.slice(0, 12).map((r) => {
      const shared = [];
      if (r.shared_listeners != null) shared.push(`${r.shared_listeners} shared listeners`);
      if (r.shared_markets) shared.push(`${r.shared_markets} shared markets`);
      if (r.shared_festival_bills) shared.push(`${r.shared_festival_bills} shared festival bills`);
      return `<tr>
        <td><b>${esc(r.artist_name)}</b><div class="small muted">${esc(r.peer_tier || "")}</div></td>
        <td><span class="badge">${money(r.shared_listeners)}</span>${r.jaccard != null ? `<div class="small muted">Jaccard ${Number(r.jaccard).toFixed(4)}</div>` : ""}</td>
        <td class="small">${shared.map((s) => `<span class="whychip">${esc(s)}</span>`).join("")}</td>
      </tr>`;
    }).join("")}</tbody></table>`;
}

function renderAlts(alts) {
  const box = document.getElementById("altsBox");
  if (!box) return;
  if (!alts.items || !alts.items.length) {
    box.innerHTML = `<div class="empty">No explainable alternatives yet.</div>`;
    return;
  }
  box.innerHTML = alts.items.slice(0, 6).map((alt) => `
    <div class="alt-card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b>${esc(alt.artist_name)}</b>
        <span><button class="btn small" data-sl="${esc(alt.artist_key)}" data-name="${esc(alt.artist_name)}">＋ Shortlist</button>
        <button class="btn small" data-compare="${esc(alt.artist_key)}">Compare</button></span>
      </div>
      <div class="why">${(alt.reasons || []).map((r) => `<span class="whychip">${esc(r)}</span>`).join("")}</div>
      <div class="small muted" style="margin-top:4px">${esc(alt.source_scope || "")}${alt.knowledge_time ? " · " + esc(String(alt.knowledge_time).slice(0, 10)) : ""}</div>
    </div>`).join("");
  box.querySelectorAll("[data-compare]").forEach((el) => {
    el.onclick = (ev) => {
      ev.stopPropagation();
      compareDraft = [alts.items[0] && box.dataset.subject, el.dataset.compare].filter(Boolean);
      location.hash = "#/compare";
    };
  });
  box.querySelectorAll("[data-sl]").forEach((el) => {
    el.onclick = async (ev) => {
      ev.stopPropagation();
      try {
        await api("/api/shortlist", { method: "POST", body: JSON.stringify({ name: el.dataset.name, artist_key: el.dataset.sl, notes: "added from alternatives" }) });
        toast("Added " + el.dataset.name + " to shortlist.");
      } catch (e) { toast(e.message); }
    };
  });
}

function renderArtistMarkets(markets) {
  const box = document.getElementById("marketsBox");
  if (!box) return;
  if (!markets.items || !markets.items.length) {
    box.innerHTML = `<div class="empty">No market evidence for this artist.</div>`;
    return;
  }
  box.innerHTML = `<table><thead><tr><th>Market</th><th>Shows</th><th>Last played</th><th>Forward</th></tr></thead><tbody>
    ${markets.items.slice(0, 12).map((m) => `<tr>
      <td><a href="#/market/${encodeURIComponent(m.market_key || m.market || m.market_name)}">${esc(m.market || m.market_name || m.market_key)}</a></td>
      <td>${money(m.observed_shows ?? m.historical_shows)}</td>
      <td>${esc(fmtDate(m.last_play_date || m.last_played))}</td>
      <td>${m.future_events != null ? m.future_events : (m.next_event ? "next " + esc(fmtDate(m.next_event.date)) : "—")}</td>
    </tr>`).join("")}</tbody></table>`;
}

function renderHistory(history) {
  const box = document.getElementById("historyBox");
  if (!box) return;
  if (!history.items || !history.items.length) {
    box.innerHTML = `<div class="empty">No live history.</div>`;
    return;
  }
  box.innerHTML = `<table><thead><tr><th>Date</th><th>Event</th><th>Venue / city</th><th>Source</th></tr></thead><tbody>
    ${history.items.slice(0, 25).map((e) => `<tr>
      <td>${esc(fmtDate(e.event_date))}</td>
      <td>${esc(e.event_name || "")}</td>
      <td>${esc([e.venue_name, e.city, e.state_code].filter(Boolean).join(", "))}</td>
      <td class="small muted">${esc(e.source_system || "")}</td>
    </tr>`).join("")}</tbody></table>` +
    (history.items.length > 25 ? `<p class="small muted">${history.items.length} retained rows</p>` : "");
}

function renderFestivals(festivals) {
  const box = document.getElementById("festBox");
  if (!box) return;
  if (!festivals.items || !festivals.items.length) {
    box.innerHTML = `<div class="empty">No festival history.</div>`;
    return;
  }
  box.innerHTML = `<table><thead><tr><th>Year</th><th>Festival</th><th>Date</th><th>Billing / stage</th></tr></thead><tbody>
    ${festivals.items.slice(0, 20).map((f) => `<tr>
      <td>${esc(f.edition_year ?? "")}</td>
      <td>${esc(f.festival_name || "")}</td>
      <td>${esc(fmtDate(f.event_date || f.performance_date))}</td>
      <td class="small">${esc([f.billing_tier, f.billing_order ? "#" + f.billing_order : "", f.stage_name].filter(Boolean).join(" · "))}</td>
    </tr>`).join("")}</tbody></table>` +
    (festivals.items.length > 20 ? `<p class="small muted">${festivals.items.length} retained rows</p>` : "");
}

function renderFuture(future) {
  const box = document.getElementById("futureBox");
  if (!box) return;
  if (!future.items || !future.items.length) {
    box.innerHTML = `<div class="empty">No forward/provider events retained.</div>`;
    return;
  }
  box.innerHTML = `<table><thead><tr><th>Date</th><th>Event</th><th>Venue / city</th><th>Advertised price</th><th>Status</th></tr></thead><tbody>
    ${future.items.slice(0, 15).map((e) => {
      const price = (e.price_min != null || e.price_max != null)
        ? `${money(e.price_min)}–${money(e.price_max)} ${esc(e.currency || "")}` : "—";
      return `<tr>
        <td>${esc(fmtDate(e.event_date))}</td>
        <td>${esc(e.event_name || e.venue_name || "")}</td>
        <td>${esc([e.venue_name, e.city, e.state_code].filter(Boolean).join(", "))}</td>
        <td>${esc(price)}</td>
        <td class="small muted">${esc(e.event_status || "")}</td>
      </tr>`;
    }).join("")}</tbody></table>
    <p class="note">${esc(future.note || "Advertised structured ranges only — not transactions or sales.")}</p>`;
}

function renderPublicTicketMarket(tm) {
  const box = document.getElementById("ticketMarketBox");
  if (!box) return;
  const events = (tm && tm.events) || [];
  if (!events.length) {
    box.innerHTML = `<div class="empty">No public ticket-market observations linked to this artist in the current serving generation.</div>`;
    return;
  }
  const fmtPrice = (p, cur, basis) => {
    if (p == null || p === "") return "— (missing)";
    return `${money(p)} ${esc(cur || "")}`.trim() + (basis ? ` <span class="small muted">[${esc(basis)}]</span>` : "");
  };
  box.innerHTML = events.slice(0, 12).map((e) => {
    const cur = e.current || {};
    const priors = e.prior_observations || [];
    const ch1 = e.change_1d && e.change_1d.status === "OBSERVED"
      ? `1D ${e.change_1d.delta >= 0 ? "+" : ""}${money(e.change_1d.delta)}` : "1D —";
    const ch7 = e.change_7d && e.change_7d.status === "OBSERVED"
      ? `7D ${e.change_7d.delta >= 0 ? "+" : ""}${money(e.change_7d.delta)}` : "7D —";
    return `<div style="margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid var(--border, #ddd)">
      <div><b>${esc(e.marketplace || "")}</b> · ${esc(fmtDate(e.event_date))} · ${esc(e.venue_name || e.city || e.event_key || "")}</div>
      <div class="small">Current @ ${esc(fmtDate(cur.observed_at))}: ${fmtPrice(cur.price, cur.currency, cur.price_basis)} · evidence ${esc(cur.evidence_status || "UNKNOWN")}</div>
      <div class="small muted">${esc(ch1)} · ${esc(ch7)} · ${e.observation_count || 1} observation(s)</div>
      ${priors.length ? `<table style="margin-top:6px"><thead><tr><th>Prior observed</th><th>Price</th><th>Basis</th><th>Evidence</th></tr></thead><tbody>
        ${priors.slice(0, 8).map((p) => `<tr>
          <td class="small">${esc(fmtDate(p.observed_at))}</td>
          <td>${p.price == null ? "— (missing)" : esc(money(p.price))}</td>
          <td class="small muted">${esc(p.price_basis || "UNKNOWN")}</td>
          <td class="small muted">${esc(p.evidence_status || "UNKNOWN")}</td>
        </tr>`).join("")}
      </tbody></table>` : `<div class="small muted">No prior observations yet.</div>`}
      ${cur.evidence_ref ? `<div class="small muted">evidence_ref ${esc(String(cur.evidence_ref).slice(0, 16))}…</div>` : ""}
    </div>`;
  }).join("");
}

function renderEvidence(evidence) {
  const box = document.getElementById("evidenceBox");
  if (!box) return;
  const items = (evidence && evidence.items) || [];
  box.innerHTML = `<table><thead><tr><th>Panel</th><th>Source</th><th>Observation time</th><th>Knowledge time</th><th>State</th></tr></thead><tbody>
    ${items.map((i) => `<tr>
      <td>${esc(i.panel)}</td>
      <td class="small muted">${esc(i.source_system || "—")}</td>
      <td class="small">${esc(fmtDate(i.observation_time))}</td>
      <td class="small">${i.knowledge_time ? esc(String(i.knowledge_time).slice(0, 10)) : "—"}</td>
      <td>${statusChip(i.status)}</td>
    </tr>`).join("")}</tbody></table>`;
}

/* ── shortlist helpers ───────────────────────────────────── */
async function quickShortlist(a, p) {
  const topMarket = (p.markets && p.markets.items && p.markets.items[0]);
  const market = topMarket ? (topMarket.market || topMarket.market_name || topMarket.market_key) : "";
  try {
    await api("/api/shortlist", { method: "POST",
      body: JSON.stringify({ name: a.name, artist_key: a.artist_key, market: market, notes: "" }) });
    toast("Added " + a.name + " to shortlist.");
  } catch (e) { toast("Shortlist failed: " + e.message); }
}

/* ── markets ─────────────────────────────────────────────── */
async function renderMarkets() {
  setNav("markets");
  view.innerHTML = `<h1>Markets</h1><input id="marketFilter" type="search" placeholder="Filter markets…" style="width:320px;margin-bottom:14px">
    <div id="marketList" class="empty">loading…</div>`;
  const listEl = document.getElementById("marketList");
  const load = async () => {
    const q = document.getElementById("marketFilter").value;
    const m = await api("/api/markets?q=" + encodeURIComponent(q) + "&limit=400");
    if (!m.items.length) { listEl.innerHTML = `<div class="empty">No markets match.</div>`; return; }
    listEl.innerHTML = m.items.map((r) => `
      <div class="market-row" data-m="${esc(r.market_key)}">
        <b>${esc(r.pretty)}</b>
        <span class="muted">${r.artist_count} artists · ${r.total_shows} observed shows · last ${esc(fmtDate(r.last_play_date))}</span>
      </div>`).join("");
    listEl.querySelectorAll(".market-row").forEach((el) => {
      el.onclick = () => location.hash = "#/market/" + encodeURIComponent(el.dataset.m);
    });
  };
  document.getElementById("marketFilter").addEventListener("input", () => load().catch(() => {}));
  try { await load(); } catch (e) { listEl.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function renderMarket(key) {
  setNav("markets");
  view.innerHTML = `<h1>Market</h1><div class="empty">loading…</div>`;
  try {
    const m = await api("/api/market/" + encodeURIComponent(key));
    view.innerHTML = `<h1>${esc(m.pretty)} <span class="badge">${m.count} artists</span></h1>
      <div class="panel"><table><thead><tr><th>Artist</th><th>Tier</th><th>Observed shows</th><th>First play</th><th>Last play</th><th>Forward</th></tr></thead><tbody>
      ${m.items.map((r) => `<tr>
        <td><a href="#/artist/${encodeURIComponent(r.artist_key)}">${esc(r.name)}</a></td>
        <td class="small muted">${esc(r.tier || "")}</td>
        <td>${money(r.observed_shows)}</td>
        <td>${esc(fmtDate(r.first_play_date))}</td>
        <td>${esc(fmtDate(r.last_play_date))}</td>
        <td>${r.future_events ? r.future_events : "—"}</td>
      </tr>`).join("")}</tbody></table></div>`;
  } catch (e) { view.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* ── compare ─────────────────────────────────────────────── */
async function renderCompare() {
  setNav("compare");
  view.innerHTML = `<h1>Compare</h1>
    <div class="grid cols2">
      <div class="panel"><label class="note">Artist A</label><input id="cmpA" type="search" placeholder="Search artist…" value="${esc(compareDraft[0] || "")}">
        <div id="cmpAResults"></div></div>
      <div class="panel"><label class="note">Artist B</label><input id="cmpB" type="search" placeholder="Search artist…" value="${esc(compareDraft[1] || "")}">
        <div id="cmpBResults"></div></div>
    </div>
    <div style="height:12px"></div>
    <div id="cmpOut" class="empty">Pick two artists to compare evidence.</div>`;

  const wire = (id, resultsId, slot) => {
    const input = document.getElementById(id);
    input.addEventListener("keydown", async (ev) => {
      if (ev.key !== "Enter" || !input.value.trim()) return;
      const hits = await api("/api/search?q=" + encodeURIComponent(input.value.trim()) + "&limit=5");
      const box = document.getElementById(resultsId);
      box.innerHTML = hits.map((h) => `<div class="result" data-k="${esc(h.entity_id)}"><b>${esc(h.name)}</b></div>`).join("");
      box.querySelectorAll(".result").forEach((el) => {
        el.onclick = () => { slot[slot.length > 1 ? 1 : 0] = el.dataset.k; loadCompare(); };
      });
    });
  };
  wire("cmpA", "cmpAResults", compareDraft);
  wire("cmpB", "cmpBResults", compareDraft);

  const loadCompare = async () => {
    if (compareDraft.length < 2 || !compareDraft[0] || !compareDraft[1]) {
      document.getElementById("cmpOut").innerHTML = `<div class="empty">Pick two artists.</div>`;
      return;
    }
    const [a, b] = compareDraft;
    try {
      const c = await api("/api/artist-security/compare?a=" + encodeURIComponent(a) + "&b=" + encodeURIComponent(b));
      document.getElementById("cmpOut").innerHTML = renderComparison(c);
    } catch (e) {
      document.getElementById("cmpOut").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    }
  };
  // Deep-link support: #/compare?a=<key>&b=<key> seeds both slots.
  const qp = new URLSearchParams(location.hash.split("?")[1] || "");
  const deepA = qp.get("a");
  const deepB = qp.get("b");
  if (deepA && deepB) {
    compareDraft = [deepA, deepB];
    document.getElementById("cmpA").value = deepA;
    document.getElementById("cmpB").value = deepB;
    loadCompare();
  } else if (compareDraft.length === 2 && compareDraft[0] && compareDraft[1]) {
    loadCompare();
  }
}

function renderComparison(c) {
  const side = (s) => `
    <div class="panel">
      <h3>${esc(s.artist_name)}</h3>
      <div><a href="#/artist/${encodeURIComponent(s.artist_key)}">open artist →</a></div>
      <table>${rowsFor(s)}</table>
    </div>`;
  const rowsFor = (s) => [
    ["Type", s.identity && s.identity.type],
    ["Area", s.identity && s.identity.area],
    ["Tier", s.identity && s.identity.tier],
    ["Live events", s.historical_events],
    ["Festival appearances", s.festival_appearances],
    ["Markets", s.market_count == null ? (s.strongest_markets || []).length : s.market_count],
    ["Forward events", s.future_events],
    ["Advertised ticket ranges", s.current_ticket_ranges],
    ["Audience peers", s.audience_peers],
    ["Attention", Object.entries(s.attention || {}).map(([k, v]) => k + ": " + v).join(", ")],
  ].map(([k, v]) => `<tr><td class="muted">${esc(k)}</td><td>${v == null ? "—" : esc(v)}</td></tr>`).join("");

  return `<div class="compare-grid">
    ${side(c.left)}${side(c.right)}
    <div class="panel" style="grid-column:1/-1"><h3>Dimensions</h3>
      ${(c.dimensions || []).map((d) => `
        <div class="dimension">
          <div class="dl">${esc(d.label)}</div>
          <div class="small muted">${esc(d.explanation || "")}</div>
          <div class="row2" style="grid-template-columns:1fr 1fr;display:grid"><span>${renderDim(d.left)}</span><span>${renderDim(d.right)}</span></div>
        </div>`).join("")}
      <p class="note">${esc(c.note || "")}</p>
    </div></div>`;
  function fmtMarket(m) {
    if (!m || typeof m !== "object") return "";
    const name = m.market_key || m.market_name || "";
    const shows = m.historical_shows != null ? `${m.historical_shows} shows` : "";
    const lastPlay = m.last_play ? ` · last ${String(m.last_play).slice(0, 10)}` : "";
    return [name, shows, lastPlay].filter(Boolean).join(" · ");
  }
  function renderDim(v) {
    if (v == null) return "<span class='muted'>—</span>";
    if (Array.isArray(v)) return v.map((m) => esc(fmtMarket(m))).join("<br>") || "<span class='muted'>—</span>";
    if (typeof v === "object") return Object.entries(v).map(([k, val]) => `${esc(k)}: ${esc(val == null ? "—" : val)}`).join("<br>");
    return esc(v);
  }
}

/* ── shortlist ───────────────────────────────────────────── */
async function renderShortlist() {
  setNav("shortlist");
  view.innerHTML = `<h1>Shortlist</h1>
    <div class="panel" style="max-width:520px;margin-bottom:14px">
      <h3>Add candidate</h3>
      <div class="form-row"><label>Name *</label><input id="slName" type="text"></div>
      <div class="form-row"><label>Market</label><input id="slMarket" type="text"></div>
      <div class="form-row"><label>Date</label><input id="slDate" type="date"></div>
      <div class="form-row"><label>Venue / capacity (optional)</label><input id="slVenue" type="text"></div>
      <div class="form-row"><label>Notes</label><textarea id="slNotes" rows="2"></textarea></div>
      <button class="primary" id="slAdd">Add to shortlist</button>
    </div>
    <div class="panel"><h3>Saved candidates</h3><div id="slList" class="empty">loading…</div></div>`;

  document.getElementById("slAdd").onclick = async () => {
    const name = document.getElementById("slName").value.trim();
    if (!name) { toast("Name required."); return; }
    try {
      await api("/api/shortlist", { method: "POST", body: JSON.stringify({
        name, market: document.getElementById("slMarket").value.trim(),
        date: document.getElementById("slDate").value,
        venue: document.getElementById("slVenue").value.trim(),
        notes: document.getElementById("slNotes").value.trim(),
      })});
      toast("Added " + name + ".");
      document.getElementById("slName").value = document.getElementById("slNotes").value = "";
      loadList();
    } catch (e) { toast(e.message); }
  };

  const loadList = async () => {
    try {
      const items = await api("/api/shortlist");
      const box = document.getElementById("slList");
      if (!items.length) { box.innerHTML = `<div class="empty">Shortlist is empty — add candidates from an artist page or above.</div>`; return; }
      box.innerHTML = `<div style="margin-bottom:8px"><button class="btn" id="slCompare">Compare selected (2)</button></div>
        <table><thead><tr><th></th><th>Name</th><th>Market</th><th>Date</th><th>Venue / capacity</th><th>Notes</th><th></th></tr></thead><tbody>
        ${items.map((it) => `<tr>
          <td><input type="checkbox" class="slPick" data-k="${esc(it.artist_key || "")}"></td>
          <td>${esc(it.name)}${it.artist_key ? `<div class="small"><a href="#/artist/${encodeURIComponent(it.artist_key)}">open artist →</a></div>` : ""}</td>
          <td>${esc(it.market || "—")}</td>
          <td>${esc(fmtDate(it.event_date))}</td>
          <td class="small">${esc([it.venue, it.capacity].filter(Boolean).join(" · ") || "—")}</td>
          <td class="small muted">${esc(it.notes || "—")}</td>
          <td><button class="danger" data-del="${esc(it.id)}">Remove</button></td>
        </tr>`).join("")}</tbody></table>`;
      const cmpBtn = document.getElementById("slCompare");
      cmpBtn.onclick = () => {
        const picked = [...box.querySelectorAll(".slPick:checked")].map((c) => c.dataset.k).filter(Boolean);
        if (picked.length !== 2) { toast("Select exactly two candidates with linked artists."); return; }
        location.hash = "#/compare?a=" + encodeURIComponent(picked[0]) + "&b=" + encodeURIComponent(picked[1]);
      };
      box.querySelectorAll("[data-del]").forEach((el) => {
        el.onclick = async () => {
          try { await api("/api/shortlist/" + encodeURIComponent(el.dataset.del), { method: "DELETE" }); toast("Removed."); loadList(); }
          catch (e) { toast(e.message); }
        };
      });
    } catch (e) { document.getElementById("slList").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  };
  loadList();
}

/* ── demo ────────────────────────────────────────────────── */
async function renderDemo() {
  setNav("demo");
  view.innerHTML = `<h1>Demo</h1>
    <div class="panel hero" style="margin-bottom:14px">
      <h3>Guided walkthrough — 3 minutes</h3>
      <p class="small muted">Follow one real booking question end-to-end. Every step uses actual evidence in this generation.</p>
      <div id="demoSteps"></div>
    </div>
    <div class="panel"><h3>All demo artists</h3>
      <p class="small muted">Ten real artists with the deepest cross-source evidence — identity, audience peers, markets,
      live history, festivals, attention and forward evidence. Click any card for the full Artist Security page.</p>
      <div id="demoGrid" class="demo-grid"><div class="empty">loading…</div></div></div>`;
  try {
    const demo = await api("/api/demo");
    if (demo.length) {
      const a = demo[0];            // Artist A — Alice Cooper
      const b = demo[1] || demo[0]; // Artist B — Barry Manilow
      const steps = [
        { n: 1, label: "Open Artist A", text: `${a.name} — what do we know?`, go: () => location.hash = "#/artist/" + encodeURIComponent(a.artist_key) },
        { n: 2, label: "Inspect market + live evidence", text: `Look at ${a.name}'s market footprint, live history and festivals.`, go: () => location.hash = "#/artist/" + encodeURIComponent(a.artist_key) },
        { n: 3, label: "View evidence-supported alternatives", text: `Why is each alternative related? (shared listeners, markets, festival bills)`, go: () => location.hash = "#/artist/" + encodeURIComponent(a.artist_key) },
        { n: 4, label: "Compare Artist A vs Artist B", text: `${a.name} vs ${b.name} — audience overlap, markets, live history.`, go: () => location.hash = "#/compare?a=" + encodeURIComponent(a.artist_key) + "&b=" + encodeURIComponent(b.artist_key) },
        { n: 5, label: "Add Artist B to your shortlist", text: `Save ${b.name} as a candidate, then reload — it persists.`, go: async () => {
            try {
              await api("/api/shortlist", { method: "POST", body: JSON.stringify({ name: b.name, artist_key: b.artist_key, notes: "added from guided demo" }) });
              toast("Added " + b.name + " to shortlist.");
              location.hash = "#/shortlist";
            } catch (e) { toast(e.message); }
          } },
      ];
      document.getElementById("demoSteps").innerHTML =
        `<ol style="margin:6px 0 0;padding-left:20px">` +
        steps.map((s) => `<li style="margin-bottom:6px"><b>${esc(s.label)}</b> — <span class="muted">${esc(s.text)}</span> <button class="btn small" data-step="${s.n}">Go →</button></li>`).join("") +
        `</ol>`;
      document.querySelectorAll("[data-step]").forEach((el) => {
        el.onclick = () => { const s = steps[Number(el.dataset.step) - 1]; if (s) s.go(); };
      });
    }
  } catch (e) { /* grid below still loads */ }
  try {
    const demo = await api("/api/demo");
    const grid = document.getElementById("demoGrid");
    if (!demo.length) { grid.innerHTML = `<div class="empty">No demo artists yet.</div>`; return; }
    grid.innerHTML = demo.map(demoCard).join("");
    grid.querySelectorAll(".demo-card").forEach((el, i) => {
      el.onclick = () => location.hash = "#/artist/" + encodeURIComponent(demo[i].artist_key);
    });
  } catch (e) { document.getElementById("demoGrid").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* ── monitor ─────────────────────────────────────────────── */
async function renderMonitor() {
  setNav("monitor");
  view.innerHTML = `<h1>Monitor</h1>
    <p class="muted">What changed for your watchlist since the last time you looked at this evidence generation.</p>
    <div class="grid cols2">
      <div class="panel"><h3>Watchlist changes</h3><div id="monBox" class="empty">loading…</div></div>
      <div class="panel"><h3>Private outcome vault</h3><div id="vaultBox" class="empty">loading…</div></div>
    </div>
    <div style="height:14px"></div>
    <div class="panel"><h3>Model readiness — nothing trained yet</h3><div id="readyBox" class="empty">loading…</div></div>`;
  try {
    const mon = await api("/api/monitor");
    const box = document.getElementById("monBox");
    if (!mon.artists || !mon.artists.length) {
      box.innerHTML = `<div class="empty">No watched artists. Add artists to your shortlist — they are watched automatically.</div>`;
    } else {
      box.innerHTML = mon.artists.map((a) => `
        <div class="alt-card">
          <b>${esc(a.artist_name)}</b>
          <div class="small muted">future ${a.current.future_events} · markets ${a.current.markets} · festivals ${a.current.festivals} · attention ${a.current.attention}</div>
          ${(a.changes.length ? a.changes.map((c) =>
            `<span class="chip obs">${esc(c.detail)}</span>`).join(" ") : `<span class="small muted">no changes since last look</span>`)}
        </div>`).join("");
    }
  } catch (e) { document.getElementById("monBox").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  try {
    const v = await api("/api/vault");
    document.getElementById("vaultBox").innerHTML =
      `<table><tbody>
         <tr><td class="muted">Private outcome entries</td><td><b>${esc(v.entries)}</b></td></tr>
         <tr><td class="muted">Hidden (not yet revealed)</td><td><b>${esc(v.hidden)}</b></td></tr>
       </tbody></table>
       <p class="note">${esc(v.privacy)}</p>`;
  } catch (e) { document.getElementById("vaultBox").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  try {
    const r = await api("/api/readiness");
    document.getElementById("readyBox").innerHTML =
      `<table><tbody>
         ${[["Private settled shows", r.private_settled_shows], ["With booking/announcement/on-sale cutoff", r.with_booking_cutoff],
           ["With leakage-safe PIT reconstruction", r.with_valid_pit_reconstruction], ["PIT insufficient", r.pit_insufficient], ["With tickets sold", r.with_tickets_sold],
           ["With gross", r.with_gross], ["With guarantee", r.with_guarantee], ["With expenses", r.with_expenses],
           ["With profit/contribution", r.with_profit_or_contribution], ["Markets", r.markets], ["Venues", r.venues],
           ["Artists", r.artists], ["Eligible OOS rows", r.eligible_oos_rows]].map(([k, v]) =>
           `<tr><td class="muted">${esc(k)}</td><td><b>${v == null ? "—" : esc(v)}</b></td></tr>`).join("")}
       </tbody></table>
       <p class="note">${esc(r.note)} · progression: ${Object.entries(r.progression || {}).map(([k, v]) => `${esc(k)} → ${esc(v)}`).join(" · ")}</p>`;
  } catch (e) { document.getElementById("readyBox").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* ── underwrite ──────────────────────────────────────────── */
let uwKey = "";

// BLANK = UNKNOWN. Explicit "0" = assumed zero. No hidden defaults.
const uwInputs = {
  usable_capacity: "", sellable_capacity: "", average_ticket_price: "",
  sell_through: "", sell_through_down: "", sell_through_up: "",
  guarantee: "", backend_percentage: "", artist_expenses: "",
  deal_type: "",
  cost_marketing: "", cost_production: "", cost_venue: "", cost_labor: "",
  cost_insurance: "", cost_other: "", tax_rate: "", ticketing_deduction: "",
  ancillary_revenue: "", sponsorship: "",
  template: "", accept_template: "",
};

async function renderUnderwrite() {
  setNav("underwrite");
  const qp = new URLSearchParams(location.hash.split("?")[1] || "");
  const aKey = qp.get("a") || "";
  const mKey = qp.get("m") || "";
  const dDate = qp.get("d") || "";
  uwInputs.event_date = dDate;
  // Shareable assumption presets: cap, sell, atp, st, bk, gt, mkt, prod, venue.
  const pre = (k, d) => { const v = qp.get(k); return v != null && v !== "" ? v : d; };
  uwInputs.usable_capacity = pre("cap", uwInputs.usable_capacity);
  uwInputs.sellable_capacity = pre("sell", uwInputs.sellable_capacity);
  uwInputs.average_ticket_price = pre("atp", uwInputs.average_ticket_price);
  uwInputs.guarantee = pre("gt", uwInputs.guarantee);
  uwInputs.sell_through = pre("st", uwInputs.sell_through);
  uwInputs.backend_percentage = pre("bk", uwInputs.backend_percentage);
  uwInputs.cost_marketing = pre("mkt", uwInputs.cost_marketing);
  uwInputs.cost_production = pre("prod", uwInputs.cost_production);
  uwInputs.cost_venue = pre("venue", uwInputs.cost_venue);

  let artistName = aKey ? "(loading…)" : "";
  view.innerHTML = `<h1>Underwrite</h1>
    <p class="muted">Pre-offer buyer brief — artist + market + your assumptions → deterministic scenario math. No BOOK/PASS, no invented guarantees.</p>
    <div class="grid cols2">
      <div class="panel"><h3>1 · What are we pricing?</h3>
        <div class="form-row"><label>Artist</label><input id="uwA" type="search" placeholder="Search artist…" value="${esc(artistName)}"><div id="uwAHits"></div></div>
        <div class="form-row"><label>Market</label><input id="uwM" type="text" placeholder="e.g. chicago-il or Chicago" value="${esc(mKey)}"></div>
        <div class="form-row"><label>Venue (optional)</label><input id="uwV" type="text" placeholder="e.g. The Vic Theatre"></div>
        <div class="form-row"><label>Event date</label><input id="uwD" type="date" value="${esc(dDate)}"></div>
      </div>
      <div class="panel"><h3>2 · Your assumptions <span class="chip unk">USER ASSUMPTION</span></h3>
        <div class="form-row"><label>Usable capacity</label><input id="uwUsable" type="number" placeholder="0" value="${esc(uwInputs.usable_capacity)}"></div>
        <div class="form-row"><label>Sellable capacity</label><input id="uwSellable" type="number" placeholder="default = usable" value="${esc(uwInputs.sellable_capacity)}"></div>
        <div class="form-row"><label>Average ticket price ($)</label><input id="uwAtp" type="number" placeholder="0" value="${esc(uwInputs.average_ticket_price)}"></div>
        <div class="form-row"><label>Base sell-through (0–1)</label><input id="uwSt" type="number" step="0.01" min="0" max="1" placeholder="blank = UNKNOWN" value="${esc(uwInputs.sell_through)}"></div>
        <div class="form-row"><label>Downside sell-through (0–1)</label><input id="uwStDown" type="number" step="0.01" min="0" max="1" placeholder="optional" value="${esc(uwInputs.sell_through_down)}"></div>
        <div class="form-row"><label>Upside sell-through (0–1)</label><input id="uwStUp" type="number" step="0.01" min="0" max="1" placeholder="optional" value="${esc(uwInputs.sell_through_up)}"></div>
        <div class="form-row"><label>Scenario template</label>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            ${Object.entries({ CONSERVATIVE: "25/45/65", MODERATE: "35/55/75", AGGRESSIVE: "45/65/85" }).map(([name, rates]) =>
              `<button class="btn small" data-tpl="${name}">${name} ${rates}</button>`).join("")}
          </div>
          <p class="small muted">Templates fill sell-through as <b>SYSTEM_TEMPLATE_ASSUMPTION</b>. Build the brief to accept; edit a field to override it with your own USER_ASSUMPTION.</p>
        </div>
      </div>
    </div>
    <div style="height:12px"></div>
    <div class="grid cols2">
      <div class="panel"><h3>3 · Proposed deal</h3>
        <div class="form-row"><label>Guarantee ($)</label><input id="uwGuarantee" type="number" placeholder="0" value="${esc(uwInputs.guarantee)}"></div>
        <div class="form-row"><label>Backend % (0–1)</label><input id="uwBackend" type="number" step="0.01" min="0" max="1" value="${esc(uwInputs.backend_percentage)}"></div>
        <div class="form-row"><label>Artist expense allowance ($)</label><input id="uwArtistExp" type="number" value="${esc(uwInputs.artist_expenses)}"></div>
        <div class="form-row"><label>Deal type</label><select id="uwDeal">${["", "GUARANTEE_VS_PERCENTAGE", "FLAT_GUARANTEE", "PERCENTAGE_OF_DEFINED_BASE"].map((t) =>
          `<option value="${t}" ${t === uwInputs.deal_type ? "selected" : ""}>${t || "(blank = UNKNOWN)"}</option>`).join("")}</select></div>
      </div>
      <div class="panel"><h3>4 · Expenses & revenue (blank = UNKNOWN, 0 = assumed zero)</h3>
        <div class="form-row"><label>Marketing</label><input id="uwMkt" type="number" value="${esc(uwInputs.cost_marketing)}"></div>
        <div class="form-row"><label>Production</label><input id="uwProd" type="number" value="${esc(uwInputs.cost_production)}"></div>
        <div class="form-row"><label>Venue rental</label><input id="uwVenue" type="number" value="${esc(uwInputs.cost_venue)}"></div>
        <div class="form-row"><label>Labor / insurance / other</label><input id="uwOther" type="text" placeholder="0,0,0" value="${esc(uwInputs.cost_labor)},${esc(uwInputs.cost_insurance)},${esc(uwInputs.cost_other)}"></div>
        <div class="form-row"><label>Ancillary revenue</label><input id="uwAnc" type="number" placeholder="blank = UNKNOWN" value="${esc(uwInputs.ancillary_revenue)}"></div>
        <div class="form-row"><label>Tax rate (0–1) · ticket deduction</label><input id="uwTaxDed" type="text" placeholder="blank,blank = UNKNOWN" value="${esc(uwInputs.tax_rate)},${esc(uwInputs.ticketing_deduction)}"></div>
      </div>
    </div>
    <div style="height:12px"></div>
    <button class="btn primary" id="uwRun">Build buyer brief →</button>
    <div style="height:14px"></div>
    <div id="briefOut" class="empty">Fill in what you know, then build the brief. Missing numbers stay UNKNOWN — never silently zeroed.</div>
    <div style="height:14px"></div>
    <div class="panel"><h3>Saved decisions</h3><div id="decList" class="empty">loading…</div></div>`;

  // Artist picker
  uwKey = aKey;
  const uwA = document.getElementById("uwA");
  if (aKey) {
    try {
      const p = await api("/api/artist-security/" + encodeURIComponent(aKey));
      uwA.value = (p.artist || {}).name || aKey;
    } catch (e) { /* keep key */ }
  }
  // Scenario template buttons (SYSTEM_TEMPLATE_ASSUMPTION until accepted).
  // Delegated so innerHTML rebuilds can never orphan the handlers.
  view.addEventListener("click", (ev) => {
    const btn = ev.target.closest && ev.target.closest("[data-tpl]");
    if (!btn) return;
    const rates = { CONSERVATIVE: ["0.25", "0.45", "0.65"], MODERATE: ["0.35", "0.55", "0.75"], AGGRESSIVE: ["0.45", "0.65", "0.85"] }[btn.dataset.tpl];
    if (!rates) return;
    document.getElementById("uwStDown").value = rates[0];
    document.getElementById("uwSt").value = rates[1];
    document.getElementById("uwStUp").value = rates[2];
    uwInputs.template = btn.dataset.tpl;
    uwInputs.accept_template = "accept";
    toast(`Template ${btn.dataset.tpl} applied — SYSTEM_TEMPLATE_ASSUMPTION. Build the brief to accept.`);
  });

  uwA.addEventListener("input", async () => {
    const q = uwA.value.trim();
    const hits = document.getElementById("uwAHits");
    if (q.length < 2) { hits.innerHTML = ""; return; }
    try {
      const res = await api("/api/search?q=" + encodeURIComponent(q) + "&limit=6");
      hits.innerHTML = res.map((h) =>
        `<div class="result" data-k="${esc(h.entity_id)}" data-name="${esc(h.name)}"><b>${esc(h.name)}</b></div>`).join("");
      hits.querySelectorAll(".result").forEach((el) => {
        el.onclick = () => { uwKey = el.dataset.k; uwA.value = el.dataset.name; hits.innerHTML = ""; };
      });
    } catch (e) { hits.innerHTML = `<span class="empty">${esc(e.message)}</span>`; }
  });

  // A deep link with a key but no fetch hit still needs uwKey set (pickup from earlier).

  document.getElementById("uwRun").onclick = runUnderwrite;
  document.getElementById("uwSt").addEventListener("keydown", (ev) => { if (ev.key === "Enter") runUnderwrite(); });
  loadDecisions();

  // Shareable one-click brief: #/underwrite?a=…&m=…&d=…&auto=1 builds immediately.
  if (qp.get("auto") === "1" && aKey) runUnderwrite();
}

function collectUwInputs() {
  const other = (document.getElementById("uwOther").value || "").split(",");
  const taxDed = (document.getElementById("uwTaxDed").value || "").split(",");
  const g = (el) => (document.getElementById(el) || {}).value ?? "";
  uwInputs.usable_capacity = g("uwUsable");
  uwInputs.sellable_capacity = g("uwSellable");
  uwInputs.average_ticket_price = g("uwAtp");
  uwInputs.sell_through = g("uwSt");
  uwInputs.sell_through_down = g("uwStDown");
  uwInputs.sell_through_up = g("uwStUp");
  uwInputs.guarantee = g("uwGuarantee");
  uwInputs.backend_percentage = g("uwBackend");
  uwInputs.deal_type = g("uwDeal");
  uwInputs.cost_marketing = g("uwMkt");
  uwInputs.cost_production = g("uwProd");
  uwInputs.cost_venue = g("uwVenue");
  uwInputs.cost_labor = (other[0] || "").trim();
  uwInputs.cost_insurance = (other[1] || "").trim();
  uwInputs.cost_other = (other[2] || "").trim();
  uwInputs.artist_expenses = g("uwArtistExp");
  uwInputs.ancillary_revenue = g("uwAnc");
  uwInputs.tax_rate = (taxDed[0] || "").trim();
  uwInputs.ticketing_deduction = (taxDed[1] || "").trim();
  uwInputs.event_date = g("uwD");
  // Template marker travels only when the buyer actually applied it this run.
  const payload = { ...uwInputs };
  if (!uwInputs.template) { delete payload.template; delete payload.accept_template; }
  return {
    artist_key: uwKey,
    market_key: g("uwM").trim() || null,
    inputs: payload,
  };
}

async function runUnderwrite() {
  const req = collectUwInputs();
  if (!req.artist_key) { toast("Pick an artist from search first."); return; }
  document.getElementById("briefOut").innerHTML = `<div class="empty">building buyer brief…</div>`;
  try {
    const b = await api("/api/underwrite", { method: "POST", body: JSON.stringify(req) });
    renderBrief(b);
  } catch (e) {
    document.getElementById("briefOut").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function prov(chip) {
  const label = String(chip || "UNKNOWN");
  const cls = label === "OBSERVED" || label === "DERIVED" ? "obs" : "unk";
  return `<span class="chip ${cls}">${esc(label)}</span>`;
}

function renderBrief(b) {
  const m = b.market || {};
  const row = (k, v, provLabel) => `<tr><td class="muted">${esc(k)}</td><td>${v == null || v === "" ? "—" : esc(v)}</td>${provLabel ? `<td>${prov(provLabel)}</td>` : ""}</tr>`;
  const economicsRows = (label, sc) => {
    if (sc.error) return `<div class="empty">${esc(sc.error)}</div>`;
    const o = sc.outputs || {};
    const gv = (k) => { const v = o[k] || {}; return { value: v.value, status: v.status, reason: v.reason }; };
    const mon = (x) => x.value == null ? (x.status === "UNKNOWN" ? `<span class="chip unk">UNKNOWN</span>` : `<span class="chip unk">${esc(x.status)}</span>`) : "$" + money(x.value);
    return `<table><tbody>
      ${row("Gross potential", mon(gv("gross_potential")), "DERIVED")}
      ${row("Gross ticket revenue", mon(gv("gross_ticket_revenue")), "DERIVED")}
      ${row("Paid tickets @ assumed sell-through", gv("paid_tickets").value, "USER ASSUMPTION")}
      ${row("Artist settlement", mon(gv("artist_settlement")), "DERIVED")}
      ${row("Promoter contribution", mon(gv("promoter_contribution")), "DERIVED")}
      ${row("Promoter margin", gv("promoter_margin").value != null ? (Number(gv("promoter_margin").value) * 100).toFixed(1) + "%" : null, "DERIVED")}
      ${row("Break-even paid tickets", gv("break_even_paid_tickets").value, "DERIVED")}
      ${row("Break-even sell-through", gv("break_even_sell_through").value != null ? (Number(gv("break_even_sell_through").value) * 100).toFixed(0) + "%" : null, "DERIVED")}
      ${row("Break-even average ticket", mon(gv("break_even_average_ticket_price")), "DERIVED")}
      ${row("Maximum flat guarantee @ break-even", mon(gv("maximum_flat_guarantee_at_break_even")), "DERIVED")}
    </tbody></table>`;
  };

  const scenariosHTML = Object.entries(b.economics || {}).map(([label, sc]) => `
    <div class="panel">
      <h3>${esc(label)} <span class="chip unk">USER-DEFINED SCENARIO</span></h3>
      ${economicsRows(label, sc)}
    </div>`).join("");
  const economicsEmpty = !Object.keys(b.economics || {}).length
    ? `<div class="panel"><h3>E · Scenario math</h3><p class="muted">No scenario sell-through entered — the brief is honest about what it cannot compute. Enter a base sell-through, or apply a template above, to run the deterministic economics.</p></div>`
    : "";
  const templateChip = b.economics_template ? ` <span class="chip unk">SYSTEM TEMPLATE ${esc(Object.values(b.economics_template)[0] || "")}</span>` : "";
  const provRows = Object.entries(b.economics_input_provenance || {}).map(([k, v]) =>
    `<tr><td class="muted">${esc(k)}</td><td>${v === "UNKNOWN" ? `<span class="chip unk">UNKNOWN</span>` : v === "SYSTEM_TEMPLATE_ASSUMPTION" ? `<span class="chip unk">SYSTEM_TEMPLATE_ASSUMPTION</span>` : prov(v)}</td></tr>`).join("");
  const provBlock = provRows
    ? `<div class="panel"><h3>Where did this number come from?</h3><table><tbody>${provRows}</tbody></table>
       <p class="small muted">BLANK = UNKNOWN · explicit 0 = assumed zero · template fills = SYSTEM_TEMPLATE_ASSUMPTION until you edit or accept.</p></div>`
    : "";

  const flagsHTML = (b.risk_flags || []).length
    ? b.risk_flags.map((f) => `<div class="alt-card"><b>${esc(f.label)}</b><div class="small muted">${esc(f.detail)}</div></div>`).join("")
    : `<div class="empty">No deterministic risk flags raised for these inputs.</div>`;

  const compsHTML = (b.comparables || []).map((c) => `
    <div class="alt-card">
      <div style="display:flex;justify-content:space-between"><b>${esc(c.artist_name)}</b>
      <button class="btn small" data-comp="${esc(c.artist_key)}">Compare →</button></div>
      <div class="why">${(c.components || []).map((r) => `<span class="whychip">${esc(r)}</span>`).join("")}</div>
    </div>`).join("");

  const competing = (b.competing_events || []).map((e) =>
    `<div class="now-row"><b>${esc(e.artist_name)}</b> · <span class="muted">${esc(fmtDate(e.event_date))} · ${esc(e.venue_city || "")}</span></div>`).join("");

  view.innerHTML += `<div id="briefWrap" class="panel hero" style="border-top:3px solid #c9a961">
    <h2>Buyer decision brief — ${esc(b.artist.name || "")} ${templateChip}<span class="badge">${esc(b.generation || "")}</span></h2>
    <p class="small muted">Evidence generation frozen here for audit. Every number below is ${prov("OBSERVED")} public evidence, ${prov("USER ASSUMPTION")} buyer input, or ${prov("UNKNOWN")} — never invented.</p>
    <div class="grid cols2">
      <div class="panel"><h3>A · Decision header</h3><table><tbody>
        ${row("Artist", b.artist.name, "OBSERVED")}${row("Tier", b.artist.tier, "OBSERVED")}
        ${row("Identity", b.artist.identity_status || "UNKNOWN", "OBSERVED")}
        ${row("Market", m.market_key ? _pretty(m.market_key) : "—")}
        ${row("Shows observed in market", m.observed_shows, "OBSERVED")}
        ${row("Last play in market", fmtDate(m.last_play_date), "OBSERVED")}
        ${row("Event date", fmtDate(uwInputs.event_date), "USER ASSUMPTION")}
      </tbody></table></div>
      <div class="panel"><h3>B · Artist state</h3><table><tbody>
        ${row("Live events", b.artist.historical_events, "OBSERVED")}
        ${row("Festival appearances", b.artist.festival_appearances, "OBSERVED")}
        ${row("Markets", b.artist.markets, "OBSERVED")}
        ${row("Audience peers", b.artist.audience_peers, "OBSERVED")}
        ${row("Forward events", b.artist.forward_events, "OBSERVED")}
        ${row("Attention sources", (b.artist.attention_sources || []).join(", ") || "UNKNOWN", "OBSERVED")}
      </tbody></table></div>
    </div>
    <div style="height:12px"></div>
    <div class="panel"><h3>C · Market state</h3>
      ${competing ? `<p class="small muted">Provider-listed shows by other artists in this market ±14 days of the planned date:</p>${competing}` : `<div class="empty">No competing forward events detected in this market window.</div>`}
    </div>
    <div style="height:12px"></div>
    ${economicsEmpty || `<div class="grid cols3">${scenariosHTML}</div>`}
    ${provBlock || ""}
    <div style="height:12px"></div>
    <div class="grid cols2">
      <div class="panel"><h3>F · Risk flags <span class="chip unk">deterministic</span></h3>${flagsHTML}</div>
      <div class="panel"><h3>D · Comparables <span class="chip unk">EVIDENCE COMPARABLE</span></h3>${compsHTML || `<div class="empty">No explainable comparables found.</div>`}</div>
    </div>
    <div style="height:12px"></div>
    <div class="panel"><h3>G · Alternatives</h3>
      <div class="why">${(b.alternatives || []).map((a) =>
        `<a class="whychip link" href="#/artist/${encodeURIComponent(a.artist_key)}">${esc(a.artist_name)}</a>`).join("") || `<span class="muted">none</span>`}</div>
    </div>
    <div style="height:12px"></div>
    <div class="grid cols2">
      <button class="btn primary" id="uwSave">Save decision snapshot</button>
      <button class="btn" id="uwCompare">Compare with a comparable</button>
    </div>
    <p class="small muted" style="margin-top:8px">${esc((b.evidence || []).length + " evidence panels carried from generation " + (b.generation || "UNKNOWN"))}</p>
  </div>`;
  document.getElementById("uwSave").onclick = async () => {
    try {
      const snap = await api("/api/underwrite/save", { method: "POST", body: JSON.stringify({
        artist_key: b.artist_key, artist_name: b.artist.name, market_key: (b.market || {}).market_key,
        venue: "", event_date: uwInputs.event_date, inputs: uwInputs, brief: b, status: "RESEARCHING",
      }) });
      toast("Snapshot saved: " + snap.snapshot_id);
      loadDecisions();
    } catch (e) { toast(e.message); }
  };
  document.getElementById("uwCompare").onclick = () => {
    const first = (b.comparables || [])[0];
    if (!first) { toast("No comparable to compare with."); return; }
    location.hash = "#/compare?a=" + encodeURIComponent(b.artist_key) + "&b=" + encodeURIComponent(first.artist_key);
  };
  document.querySelectorAll("[data-comp]").forEach((el) => {
    el.onclick = () => location.hash = "#/compare?a=" + encodeURIComponent(b.artist_key) + "&b=" + encodeURIComponent(el.dataset.comp);
  });
}

function _pretty(marketKey) {
  const parts = String(marketKey || "").split("-").filter(Boolean);
  if (!parts.length) return "";
  if (parts.length === 1) return parts[0][0].toUpperCase() + parts[0].slice(1);
  const city = parts.slice(0, -1).map((p) => p[0].toUpperCase() + p.slice(1)).join(" ");
  return city + ", " + parts[parts.length - 1].toUpperCase();
}

async function loadDecisions() {
  const box = document.getElementById("decList");
  if (!box) return;
  try {
    const items = await api("/api/decisions");
    if (!items.length) { box.innerHTML = `<div class="empty">No saved decisions yet. Save a brief to revisit it later.</div>`; return; }
    box.innerHTML = `<table><thead><tr><th>Artist</th><th>Market</th><th>Date</th><th>Status</th><th></th><th></th></tr></thead><tbody>
      ${items.map((d) => `<tr>
        <td><a href="#/artist/${encodeURIComponent(d.artist_key)}">${esc(d.artist_name)}</a></td>
        <td>${esc(d.market_key ? _pretty(d.market_key) : "—")}</td>
        <td>${esc(fmtDate(d.event_date))}</td>
        <td><select data-status="${esc(d.snapshot_id)}">${DECISION_STATUSES.map((s) => `<option ${s === d.status ? "selected" : ""}>${s}</option>`).join("")}</select></td>
        <td><button class="btn small" data-close="${esc(d.snapshot_id)}">Close out</button></td>
        <td><button class="danger" data-del="${esc(d.snapshot_id)}">Del</button></td>
      </tr>`).join("")}</tbody></table>`;
    let closeTarget = null;
    box.querySelectorAll("[data-status]").forEach((el) => {
      el.onchange = async () => {
        try { await api("/api/decisions/" + encodeURIComponent(el.dataset.status) + "/status", { method: "POST", body: JSON.stringify({ status: el.value }) }); toast("Status → " + el.value); }
        catch (e) { toast(e.message); }
      };
    });
    box.querySelectorAll("[data-close]").forEach((el) => {
      el.onclick = () => {
        closeTarget = el.dataset.close;
        const row = el.closest("tr");
        const name = row ? row.cells[0].textContent : "this show";
        const actuals = prompt(`Close out ${name} — enter: paid tickets | scanned | gross | artist settlement | contribution (pipe-separated, empty = unknown)`);
        if (actuals == null) return;
        const parts = actuals.split("|").map((s) => s.trim());
        if (!parts.some((p) => p)) { toast("No actuals entered — nothing stored."); return; }
        api("/api/decisions/" + encodeURIComponent(closeTarget) + "/closeout", { method: "POST", body: JSON.stringify({ actuals: {
          paid_tickets: parts[0] || "", scanned_attendance: parts[1] || "",
          ticket_gross: parts[2] || "", settlement_net: parts[3] || "",
          promoter_contribution: parts[4] || "",
        } }) }).then((r) => { toast("Close-out stored as OBSERVED_PRIVATE (" + (r.vault_id || "") + ")"); }).catch((e) => toast(e.message));
      };
    });
    box.querySelectorAll("[data-del]").forEach((el) => {
      el.onclick = async () => {
        if (!confirm("Delete this decision snapshot?")) return;
        // no DELETE API — clear the vault ties instead; keep the record honest
        toast("Snapshots are append-only in this version.");
      };
    });
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
const DECISION_STATUSES = ["RESEARCHING", "INTEREST", "HOLD", "OFFER_SENT", "PASSED", "CONFIRMED"];

async function renderPIT(showId) {
  setNav("backtest");
  view.innerHTML = `<h1>Backtest — show</h1><div class="empty">loading…</div>`;
  try {
    const s = await api("/api/backtest/show/" + encodeURIComponent(showId));
    const pit = s.pit || {};
    let pitHTML;
    if (pit.status === "PIT_INSUFFICIENT") {
      pitHTML = `<div class="empty">${esc(pit.reason || "no decision cutoff")}</div>`;
    } else {
      pitHTML = `<table><tbody>
        <tr><td class="muted">Decision cutoff</td><td>${esc(s.decision_cutoff || "UNKNOWN")}</td></tr>
        <tr><td class="muted">Public live events before cutoff</td><td>${esc(pit.prior_live_events)}</td></tr>
        <tr><td class="muted">Markets with play before cutoff</td><td>${esc(pit.prior_markets)}</td></tr>
        <tr><td class="muted">Festival appearances before cutoff</td><td>${esc(pit.prior_festivals)}</td></tr>
        <tr><td class="muted">Attention observations @ cutoff</td><td>${esc(pit.prior_attention_observations)}</td></tr>
      </tbody></table>
      <p class="note">${esc(pit.note || "")}</p>`;
    }
    const outcomeHTML = (s.realized_outcome || []).length
      ? `<table><thead><tr><th>Field</th><th>Realized</th><th>Provenance</th></tr></thead><tbody>
         ${s.realized_outcome.map((o) => `<tr><td>${esc(o.label)}</td><td>${esc(o.value)}</td><td>${prov("OBSERVED_PRIVATE")}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">No realized outcomes recorded for this show.</div>`;
    view.innerHTML = `
    <h1>Backtest — ${esc(s.artist_name || showId)} <span class="badge">${esc(fmtDate(s.event_date))}</span></h1>
    <p class="small muted">${esc([s.venue, s.market].filter(Boolean).join(" · ") || "")} · <a href="#/backtest">← back to retrospective</a></p>
    <div class="grid cols2">
      <div class="panel"><h3>Decision-time evidence (what was knowable ${s.decision_cutoff ? "before " + esc(s.decision_cutoff) : "—"})</h3>${pitHTML}</div>
      <div class="panel"><h3>Realized outcome</h3>${outcomeHTML}</div>
    </div>
    <p class="note">${esc(s.note || "")}</p>`;
  } catch (e) { view.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* ── backtest ────────────────────────────────────────────── */
async function renderBacktest() {
  setNav("backtest");
  view.innerHTML = `<h1>Backtest my shows</h1>
    <p class="muted">Upload your historical show history (CSV/TSV/XLSX→CSV). Columns are mapped conservatively; buyer-level PII is quarantined and never read. Stays <b>PRIVATE_ONLY</b> in your workspace.</p>
    <div class="panel">
      <h3>1 · Upload</h3>
      <input id="btFile" type="file" accept=".csv,.tsv,.tab,.txt,.xlsx">
      <p class="small muted">No file handy? Load the bundled design-partner template (download <a href="/static/design_partner_show_history_template.csv" download>template.csv</a>).</p>
    </div>
    <div id="btPreview" class="empty">Choose a file to preview column mapping and PII quarantine.</div>
    <div style="height:14px"></div>
    <div class="panel"><h3>2 · Retrospective — what your history shows (OBSERVED_PRIVATE)</h3><div id="btRetro" class="empty">Import shows to populate.</div></div>
    <div style="height:14px"></div>
    <div class="panel"><h3>3 · Point-in-time drill-down</h3><div id="btPit" class="empty">Pick a show to see decision-time evidence vs realized outcome.</div></div>`;

  document.getElementById("btFile").addEventListener("change", async (ev) => {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    let body = { file_name: file.name };
    if (file.name.toLowerCase().endsWith(".xlsx")) {
      const buf = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      buf.forEach((b) => { binary += String.fromCharCode(b); });
      body.content_b64 = btoa(binary);
    } else {
      body.content = await file.text();
    }
    document.getElementById("btPreview").innerHTML = `<div class="empty">previewing ${esc(file.name)}…</div>`;
    try {
      const p = await api("/api/backtest/preview", { method: "POST", body: JSON.stringify(body) });
      renderBacktestPreview(p, body);
    } catch (e) { document.getElementById("btPreview").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  });
  loadRetro();
}

function renderBacktestPreview(p, contentBody) {
  const mapRows = (p.mapping || []).map((m) => `
    <tr>
      <td>${esc(m.header)}</td>
      <td><select data-map="${esc(m.header)}">
        <option value="">(ignore)</option>
        ${(m.candidates || []).map((c) => `<option ${c === m.canonical_field ? "selected" : ""}>${c}</option>`).join("")}
        ${m.canonical_field && !(m.candidates || []).includes(m.canonical_field) ? `<option selected>${esc(m.canonical_field)}</option>` : ""}
      </select></td>
      <td>${esc(m.status)}</td>
    </tr>`).join("");
  const piiList = (p.prohibited_pii || []).map((h) => esc(h)).join(", ") || "none";
  const piiNote = p.pii_redacted && p.pii_redacted.length
    ? ` — PII values <b>redacted</b> in this preview and never sent back` : "";
  const previewTable = `<table><thead><tr>${(p.headers || []).slice(0, 8).map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>
    ${(p.preview_rows || []).map((r) => `<tr>${(p.headers || []).slice(0, 8).map((h) => `<td>${esc(String(r[h] ?? "").slice(0, 24))}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  document.getElementById("btPreview").innerHTML = `
    <div class="panel" style="margin-top:12px;border-left:3px solid ${(p.prohibited_pii || []).length ? "#e05d57" : "#3da44b"}">
      <h3>Column mapping — ${p.row_count} rows · ${p.auto_mapped} auto-mapped</h3>
      <table><thead><tr><th>Header</th><th>Maps to</th><th>Status</th></tr></thead><tbody>${mapRows}</tbody></table>
      <h3>PII quarantine</h3><p class="small"><b>${esc(piiList || "no PII columns detected")}</b>${piiNote} — prohibited/potential PII columns are never read into analytics.</p>
      <h3>Preview (first 5 rows, first 8 columns)</h3>${previewTable}
      <div style="margin-top:10px"><button class="btn primary" id="btCommit">Import ${p.row_count} rows (PRIVATE_ONLY)</button></div>
    </div>`;
  document.getElementById("btCommit").onclick = async () => {
    const forced = {};
    document.querySelectorAll("[data-map]").forEach((el) => { if (el.value) forced[el.dataset.map] = el.value; });
    // Commit sends the RAW file; the server re-parses with the robust parser so
    // preview and commit always agree (quoted commas, multiline, BOM, xlsx).
    try {
      const r = await api("/api/backtest/commit", { method: "POST", body: JSON.stringify({
        file_name: p.file_name, headers: p.headers, content: contentBody.content || "",
        content_b64: contentBody.content_b64 || "",
        mapping: p.mapping, forced_mapping: forced,
      }) });
      toast(`Imported ${r.rows_imported} shows · ${r.artists_resolved} VERIFIED_EXACT · ${r.identity_review_required || 0} need review`);
      loadRetro();
    } catch (e) { toast(e.message); }
  };
}

async function loadRetro() {
  const box = document.getElementById("btRetro");
  if (!box) return;
  try {
    const r = await api("/api/backtest");
    if (r.status === "NO_PRIVATE_HISTORY") { box.innerHTML = `<div class="empty">No private show history connected — import a file above. Public MVP never requires it.</div>`; document.getElementById("btPit").innerHTML = `<div class="empty">No shows yet.</div>`; return; }
    const distRow = (name, d) => d.count ? `<tr><td class="muted">${esc(name)}</td><td>${d.count} shows</td><td>${money(d.p25)}</td><td>${money(d.median)}</td><td>${money(d.p75)}</td><td>${money(d.max)}</td></tr>` : `<tr><td class="muted">${esc(name)}</td><td colspan="5">UNKNOWN — no observed private values</td></tr>`;
    box.innerHTML = `<p class="small muted">Distributions are OBSERVED_PRIVATE only — never mixed with public serving numbers.</p>
      <table><thead><tr><th>Metric</th><th>n</th><th>p25</th><th>median</th><th>p75</th><th>max</th></tr></thead><tbody>
        ${distRow("Sell-through", r.distributions.sell_through)}
        ${distRow("Gross", r.distributions.gross)}
        ${distRow("Guarantee", r.distributions.guarantee)}
        ${distRow("Contribution", r.distributions.contribution)}
      </tbody></table>
      <div class="grid cols3" style="margin-top:10px">
        <div><h4>Top artists</h4>${(r.top_artists || []).map(([k, v]) => `<div class="small">${esc(k)} — ${v}</div>`).join("")}</div>
        <div><h4>Top markets</h4>${(r.top_markets || []).map(([k, v]) => `<div class="small">${esc(k)} — ${v}</div>`).join("")}</div>
        <div><h4>Top venues</h4>${(r.top_venues || []).map(([k, v]) => `<div class="small">${esc(k)} — ${v}</div>`).join("")}</div>
      </div>
      <p class="small muted" style="margin-top:8px">Point-in-time drill-down:</p>
      ${(r.show_ids || []).length ? r.show_ids.map((id) => `<a class="whychip link" href="#/backtest/show/${encodeURIComponent(id)}">${esc(id)}</a>`).join("") : `<span class="muted">no shows with PIT view yet</span>`}`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* ── wire up ─────────────────────────────────────────────── */
window.addEventListener("hashchange", route);

document.getElementById("searchForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = document.getElementById("searchInput").value;
  if (q.trim()) doSearch(q);
});

document.getElementById("searchInput").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    const q = document.getElementById("searchInput").value;
    if (q.trim()) doSearch(q);
  }
});

/* ── portfolio / lineup risk + sales pace (decision moat) ── */
function _moneyCell(v) {
  if (v == null || v === "") return `<span class="muted">UNKNOWN</span>`;
  return `<b>$${money(v)}</b>`;
}

function _pctCell(v) {
  if (v == null || v === "") return `<span class="muted">UNKNOWN</span>`;
  const n = Number(v);
  return Number.isFinite(n) ? (n * 100).toFixed(0) + "%" : esc(v);
}

function _exposureTable(ex) {
  const rows = [
    ["Events in portfolio", ex.events],
    ["Total guarantee exposure", ex.total_guarantee == null ? `UNKNOWN (${ex.guarantee_known}/${ex.events} known)` : `$${money(ex.total_guarantee)} · ${ex.guarantee_known}/${ex.events} events`],
    ["Base contribution (buyer scenarios)", ex.base_contribution_sum == null ? `UNKNOWN (${ex.base_contribution_known} known)` : `$${money(ex.base_contribution_sum)} · ${ex.base_contribution_known} events`],
    ["Downside contribution (buyer scenarios)", ex.downside_contribution_sum == null ? `UNKNOWN (${ex.downside_contribution_known} known)` : `$${money(ex.downside_contribution_sum)} · ${ex.downside_contribution_known} events`],
    ["Events below breakeven at base", ex.events_below_breakeven_at_base],
  ];
  return `<table><tbody>${rows.map(([k, v]) => `<tr><td class="muted">${esc(k)}</td><td><b>${esc(String(v))}</b></td></tr>`).join("")}</tbody></table>
    <p class="note">${esc("totals sum KNOWN values only; UNKNOWN is never zeroed")}</p>`;
}

async function renderPortfolio() {
  setNav("portfolio");
  view.innerHTML = `<h1>Portfolio / Lineup Risk</h1>
    <p class="muted">Aggregate your saved decision briefs into portfolio-level guarantee exposure, breakeven exposure and concentration — then stress the book with deterministic re-runs of your own scenarios.</p>
    <div class="panel">
      <h3>Lineups (named portfolios)</h3>
      <div class="form-row"><label>Name</label><input id="lpName" type="text" placeholder="e.g. Summer festival slate"></div>
      <div class="form-row"><label>Budget (manual)</label><input id="lpBudget" type="text" placeholder="e.g. 250000"></div>
      <button class="btn primary" id="lpCreate">Create lineup →</button>
      <div id="lpList" class="empty" style="margin-top:8px">loading…</div>
      <div id="lpDetail"></div>
    </div>
    <div style="height:14px"></div>
    <div class="panel">
      <h3>All saved decisions (status ≠ PASSED)</h3>
      <div id="pfExposure" class="empty">loading…</div>
      <div id="pfStress" class="empty">loading…</div>
      <div id="pfConcentration"></div>
      <div id="pfEvents" class="empty">loading…</div>
    </div>
    <div style="height:14px"></div>
    <div class="panel">
      <h3>Private sales pace tape</h3>
      <p class="small muted">Observed ticket snapshots per event (PRIVATE_ONLY). Derived sell-through/ATP are labeled DERIVED and never interpolated.</p>
      <div class="form-row"><label>Import (paste CSV rows)</label><textarea id="paceCsv" rows="3" placeholder="artist_name,venue_name,event_date,onsale_date,snapshot_at,tickets_sold,tickets_available,capacity,ticket_gross"></textarea></div>
      <button class="btn" id="paceImport">Import pace snapshots</button>
      <div id="paceList" class="empty" style="margin-top:8px">loading…</div>
      <div id="paceDetail"></div>
    </div>`;

  document.getElementById("lpCreate").onclick = async () => {
    const name = document.getElementById("lpName").value.trim();
    if (!name) { toast("lineup needs a name"); return; }
    try {
      const lp = await api("/api/portfolio/lineup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, budget: document.getElementById("lpBudget").value.trim() || null }) });
      toast("lineup created — add decisions to it");
      loadLineups();
    } catch (e) { toast(e.message); }
  };

  document.getElementById("paceImport").onclick = async () => {
    const text = document.getElementById("paceCsv").value.trim();
    if (!text) { toast("paste pace CSV rows first"); return; }
    const lines = text.split(/\r?\n/).filter((l) => l.trim());
    const rows = lines.map((l) => {
      const p = l.split(",").map((c) => c.trim());
      return { artist_name: p[0], venue_name: p[1], event_date: p[2], onsale_date: p[3], snapshot_at: p[4], tickets_sold: p[5], tickets_available: p[6], capacity: p[7], ticket_gross: p[8] };
    });
    try {
      const res = await api("/api/pace/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows }) });
      toast(`imported ${res.snapshots} snapshots across ${res.events} events (${res.skipped} skipped)`);
      loadPace();
    } catch (e) { toast(e.message); }
  };

  loadLineups();
  loadPortfolio();
  loadPace();
}

async function loadPortfolio() {
  try {
    const pf = await api("/api/portfolio");
    const all = pf.all_decisions || {};
    const ex = all.exposure || {};
    document.getElementById("pfExposure").innerHTML = ex.events
      ? `<h4>Exposure</h4>${_exposureTable(ex)}`
      : `<div class="empty">No saved decision briefs yet — build an <a href="#/underwrite">Underwrite</a> brief and save it, then return here to see book-level risk.</div>`;
    const st = all.stress || {};
    document.getElementById("pfStress").innerHTML = Object.keys(st).length
      ? `<h4>Deterministic stress (your saved base scenarios, one input changed)</h4>${Object.entries(st).map(([k, s]) => `
         <div class="alt-card">
           <b>${esc(k.replace(/_/g, " "))}</b>
           <div class="small muted">sum contribution: ${s.sum_contribution == null ? "UNKNOWN" : "$" + money(s.sum_contribution)} · ${esc(s.events_stressed)} events stressed · ${esc(s.not_applicable)} not applicable (input UNKNOWN)</div>
           <p class="note">${esc(s.label)}</p>
         </div>`).join("")}`
      : `<div class="empty">No stress rows — stress requires a saved base scenario with the shocked input known.</div>`;
    const conc = all.concentration || {};
    const cal = all.calendar || {};
    document.getElementById("pfConcentration").innerHTML = `<div class="grid cols2">` +
      `<div class="panel"><h4>Concentration (observed ratios)</h4><table><tbody>` +
      Object.entries(conc.markets || {}).slice(0, 6).map(([k, v]) => `<tr><td class="muted">${esc(k)}</td><td><b>${esc(v)}</b></td></tr>`).join("") +
      `<tr><td class="muted">Top-event guarantee share</td><td>${conc.high_guarantee_share == null ? `<span class="muted">UNKNOWN</span>` : _pctCell(String(conc.high_guarantee_share))}</td></tr>` +
      `</tbody></table></div>` +
      `<div class="panel"><h4>Calendar + capacity</h4><table><tbody>` +
      `<tr><td class="muted">Max events in any 30-day window</td><td><b>${esc(cal.max_events_in_30d_window ?? "—")}</b></td></tr>` +
      Object.entries(conc.capacity_bands || {}).map(([k, v]) => `<tr><td class="muted">Capacity band ${esc(k)}</td><td><b>${esc(v)}</b></td></tr>`).join("") +
      `</tbody></table></div></div>`;
    const evs = (all.events || []).slice(0, 30);
    document.getElementById("pfEvents").innerHTML = evs.length
      ? `<h4>Events (${esc(all.events.length)} total)</h4><table><thead><tr><th>Artist</th><th>Market</th><th>Date</th><th>Guarantee</th><th>Base contrib</th><th>Downside contrib</th><th>BE sell-through</th><th>Status</th></tr></thead><tbody>` +
        evs.map((e) => `<tr>
          <td><a href="#/artist/${encodeURIComponent(e.artist_key || "")}">${esc(e.artist_name || "?")}</a></td>
          <td class="muted">${esc(e.market || "")}</td><td class="muted">${esc(fmtDate(e.event_date))}</td>
          <td>${_moneyCell(e.guarantee)}</td><td>${_moneyCell(e.base_contribution)}</td><td>${_moneyCell(e.downside_contribution)}</td>
          <td>${e.breakeven_sell_through != null ? _pctCell(String(e.breakeven_sell_through))
              : (e.breakeven_status === "NOT_ACHIEVABLE" ? `<span class="chip unk" title="${esc(e.breakeven_reason || "")}">not achievable at cap</span>` : `<span class="muted">UNKNOWN</span>`)}</td>
          <td><span class="chip">${esc(e.status || "")}</span></td></tr>`).join("") + `</tbody></table>`
      : `<div class="empty">No events.</div>`;
    const lps = pf.lineups || [];
    if (lps.length) { await loadLineups(); }
  } catch (e) {
    document.getElementById("pfExposure").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

async function loadLineups() {
  try {
    const raw = await api("/api/portfolio");
    const box = document.getElementById("lpList");
    if (!raw.lineups.length) {
      box.innerHTML = `<div class="empty">No lineups yet. Create one, then add decisions from the table below.</div>`;
      return;
    }
    box.innerHTML = raw.lineups.map((l) => `
      <div class="alt-card">
        <b>${esc(l.name)}</b> <span class="small muted">${esc(l.member_count)} decisions · budget ${l.budget ? "$" + money(l.budget) : "—"}</span>
        <button class="btn small" data-lp="${esc(l.lineup_id)}">View risk</button>
      </div>`).join("");
    box.querySelectorAll("button[data-lp]").forEach((b) => {
      b.onclick = async () => {
        try {
          const risk = await api("/api/portfolio/lineup/" + encodeURIComponent(b.dataset.lp));
          document.getElementById("lpDetail").innerHTML = `<div class="alt-card"><b>${esc((raw.lineups.find((x) => x.lineup_id === b.dataset.lp) || {}).name)}</b>${_exposureTable(risk.exposure || {})}</div>`;
        } catch (e) { toast(e.message); }
      };
    });
  } catch (e) { document.getElementById("lpList").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function loadPace() {
  try {
    const data = await api("/api/pace");
    const box = document.getElementById("paceList");
    if (!data.events.length) {
      box.innerHTML = `<div class="empty">No private pace tape yet. Paste observed snapshots above (one row per snapshot) — or use the <a href="#/backtest">Backtest</a> show-history import for settled outcomes.</div>`;
      return;
    }
    box.innerHTML = data.events.map((e) => `
      <div class="alt-card">
        <b>${esc(e.artist_name)}</b> <span class="small muted">${esc(e.event_date)} · ${esc(e.venue_name || "")} · ${esc(e.market || "")}</span>
        <div class="small muted">${esc(e.snapshot_count)} observed snapshots · latest ${esc(e.latest_snapshot || "")} · ${e.latest_sold == null ? "" : esc(e.latest_sold) + " sold"}</div>
        <button class="btn small" data-pe="${esc(e.event_id)}">Sales curve</button>
      </div>`).join("");
    box.querySelectorAll("button[data-pe]").forEach((b) => {
      b.onclick = async () => {
        try {
          const curve = await api("/api/pace/event/" + encodeURIComponent(b.dataset.pe));
          const snaps = (curve.snapshots || []).map((s) => `<tr><td class="muted">${esc(s.snapshot_at)}</td><td>${esc(s.days_to_event ?? "")}</td><td>${esc(s.tickets_sold ?? "")}</td><td>${esc(s.tickets_available ?? "")}</td><td>${esc(s.sell_through_derived ?? "UNKNOWN")}</td><td>${esc(s.atp_derived ?? "UNKNOWN")}</td><td class="muted">${esc(s.source || "")}</td></tr>`).join("");
          const markers = (curve.pace_markers || []).map((m) => `<span class="chip obs" title="${esc(m.basis)}">${esc(m.label)}: ${esc(m.tickets_sold ?? "UNKNOWN")} sold (nearest actual)</span>`).join(" ");
          document.getElementById("paceDetail").innerHTML =
            `<div class="alt-card"><b>${esc(curve.event.artist_name)}</b> <span class="small muted">${esc(curve.event.event_date)} · ${esc(curve.event.venue_name || "")} · ${esc(curve.event.market || "")}</span>
             <p class="note">sell-through and ATP are DERIVED from actual sold+available/gross; pace markers are the nearest ACTUAL observation, never interpolated.</p>
             ${markers}
             <table style="margin-top:6px"><thead><tr><th>Snapshot</th><th>Days to event</th><th>Sold</th><th>Available</th><th>Sell-through (derived)</th><th>ATP (derived)</th><th>Source</th></tr></thead><tbody>${snaps || `<tr><td colspan="7" class="muted">no snapshots</td></tr>`}</tbody></table>
             </div>`;
        } catch (e) { toast(e.message); }
      };
    });
  } catch (e) { document.getElementById("paceList").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

route();