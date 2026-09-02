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

async function api(path, opts) {
  const res = await fetch(path, opts);
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

function route() {
  const raw = location.hash.replace(/^#\/?/, "").split("?")[0];
  const parts = raw.split("/").map(decodeURIComponent).filter((p) => p.length);
  if (!parts.length) { renderHome(); return; }
  const [head, ...rest] = parts;
  if (head === "artist" && rest.length) renderArtist(rest.join("/"));
  else if (head === "search" && rest.length) doSearch(rest.join(" "));
  else if (head === "market" && rest.length) renderMarket(rest.join("/"));
  else if (head === "markets") renderMarkets();
  else if (head === "compare") renderCompare();
  else if (head === "shortlist") renderShortlist();
  else if (head === "demo") renderDemo();
  else if (head === "home") renderHome();
  else renderHome();
}

/* ── home ────────────────────────────────────────────────── */
async function renderHome() {
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
    const c = cov.counts || {};
    document.getElementById("freshLine").innerHTML =
      `<span class="badge">${esc(cov.generation || "")}</span> ` +
      `${(c.artists ?? 0).toLocaleString()} artists · ${(c.event_history ?? 0).toLocaleString()} live events · ` +
      `${(c.artist_peers ?? 0).toLocaleString()} audience peer edges · ${(c.future_events ?? 0).toLocaleString()} forward events` +
      ` · built ${esc(String(cov.built_at || "").slice(0, 10))} · ${esc(cov.validation_status || "")}` +
      ` — <span class="muted">UNKNOWN ≠ 0 · audience sample ≠ total fans</span>`;
  } catch (e) { document.getElementById("freshLine").innerHTML = `<span class="empty">${esc(e.message)}</span>`; }

  // Now strip: upcoming forward shows + recent live activity.
  try {
    const n = await api("/api/now");
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
  } catch (e) { document.getElementById("nowBox").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }

  // Shortlist preview: what am I currently evaluating?
  try {
    const sl = await api("/api/shortlist");
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
  } catch (e) { document.getElementById("slBox").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }

  try {
    const demo = await api("/api/demo");
    const strip = document.getElementById("demoStrip");
    if (!demo.length) { strip.innerHTML = `<div class="empty">No demo artists yet.</div>`; return; }
    strip.innerHTML = demo.slice(0, 6).map((d) => demoCard(d)).join("");
    strip.querySelectorAll(".demo-card").forEach((el, i) => {
      el.onclick = () => { location.hash = "#/artist/" + encodeURIComponent(demo[i].artist_key); };
    });
  } catch (e) { document.getElementById("demoStrip").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  try {
    const m = await api("/api/markets?limit=8");
    const strip = document.getElementById("marketStrip");
    if (!m.items.length) { strip.innerHTML = `<div class="empty">No market links materialized.</div>`; return; }
    strip.innerHTML = m.items.map((r) => `
      <div class="market-row" data-m="${esc(r.market_key)}">
        <b>${esc(r.pretty)}</b><span class="muted">${r.artist_count} artists · ${r.total_shows} observed shows</span>
      </div>`).join("");
    strip.querySelectorAll(".market-row").forEach((el) => {
      el.onclick = () => location.hash = "#/market/" + encodeURIComponent(el.dataset.m);
    });
  } catch (e) { document.getElementById("marketStrip").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
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
  <div class="panel"><h3>Evidence summary</h3><div id="evidenceBox"></div></div>`;

  document.getElementById("shortlistBtn").onclick = () => quickShortlist(a, p);
  document.getElementById("compareBtn").onclick = () => {
    compareDraft = [a.artist_key];
    location.hash = "#/compare";
  };

  renderAttention(p.attention || {});
  renderPeers(p.peers);
  renderAlts(p.alternatives);
  renderArtistMarkets(p.markets);
  renderHistory(p.history);
  renderFestivals(p.festivals);
  renderFuture(p.future);
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

route();