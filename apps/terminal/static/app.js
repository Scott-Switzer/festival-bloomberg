/* Festival Intelligence terminal SPA — read-only over /api. */
(function () {
  "use strict";

  var content = document.getElementById("content");
  var searchInput = document.getElementById("search");
  var searchResults = document.getElementById("search-results");
  var navButtons = Array.prototype.slice.call(document.querySelectorAll(".nav-btn"));

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function api(path, opts) {
    opts = opts || {};
    var init = {};
    if (opts.method) {
      init.method = opts.method;
      if (opts.body) {
        init.headers = { "Content-Type": "application/json" };
        init.body = opts.body;
      }
    }
    var res = await fetch(path, init);
    if (res.status === 404) return null;
    return res.json();
  }

  function setNav(active) {
    navButtons.forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-view") === active);
    });
  }

  function linkTo(type, id, label, ctx) {
    return '<button class="link" data-nav="' + type + '" data-id="' + esc(id) + '"' +
      (ctx ? ' data-ctx="' + esc(JSON.stringify(ctx)).replace(/"/g, "&quot;") + '"' : "") +
      ">" + esc(label) + "</button>";
  }

  /* ---- terminal workflow memory (local only, no backend account) -------- */
  var RECENTS_KEY = "fi.recents.v1";
  function pushRecent(type, id, name) {
    try {
      var recents = JSON.parse(localStorage.getItem(RECENTS_KEY) || "[]");
      recents = recents.filter(function (r) { return !(r.type === type && r.id === id); });
      recents.unshift({ type: type, id: id, name: name || id, at: new Date().toISOString() });
      localStorage.setItem(RECENTS_KEY, JSON.stringify(recents.slice(0, 24)));
    } catch (e) { /* storage unavailable — recents degrade to empty */ }
  }
  function getRecents(types) {
    try {
      var recents = JSON.parse(localStorage.getItem(RECENTS_KEY) || "[]");
      if (types && types.length) recents = recents.filter(function (r) { return types.indexOf(r.type) >= 0; });
      return recents;
    } catch (e) { return []; }
  }

  function deltaChip(change, changePct) {
    if (change == null) return '<span class="unknown">UNKNOWN</span>';
    var cls = change > 0 ? "up" : change < 0 ? "down" : "flat";
    var arrow = change > 0 ? "▲" : change < 0 ? "▼" : "＝";
    var pct = changePct != null ? " (" + (changePct > 0 ? "+" : "") + changePct + "%)" : "";
    return '<span class="delta ' + cls + '">' + arrow + " " + esc(String(Math.round(change))) + (pct ? '<span class="muted">' + esc(pct) + "</span>" : "") + "</span>";
  }

  /* Compact one-line state for sections with no evidence — no empty grids. */
  function compactEmpty(title, note, lastChecked, extra) {
    return '<div class="compact-none"><span class="none-label">' + esc(title) + '</span>' +
      '<span class="muted">' + esc(note || "No observations in this serving generation.") + '</span>' +
      (lastChecked ? '<span class="muted">last checked ' + esc(String(lastChecked).slice(0, 19)) + '</span>' : "") +
      (extra || "") + '</div>';
  }

  /* Source-separated SVG line chart. Points must share one metric + window. */
  function attentionChart(series, windowDays) {
    if (!series || !series.points || series.points.length < 2) {
      return '<div class="compact-none"><span class="none-label">CHART</span><span class="muted">Insufficient observations — chart appears when at least two comparable observations of the same window land.</span></div>';
    }
    var pts = series.points;
    if (windowDays) {
      var cutoff = new Date(Date.now() - windowDays * 86400000).toISOString().slice(0, 10);
      var filtered = pts.filter(function (p) { return p.t >= cutoff; });
      if (filtered.length >= 2) pts = filtered;
    }
    var W = 640, H = 160, PAD = 8;
    var xs = pts.map(function (p) { return p.t; });
    var vs = pts.map(function (p) { return p.v; });
    var t0 = new Date(xs[0]).getTime(), t1 = new Date(xs[xs.length - 1]).getTime();
    var span = Math.max(t1 - t0, 1);
    var v0 = Math.min.apply(null, vs), v1 = Math.max.apply(null, vs);
    var vspan = Math.max(v1 - v0, 1);
    var path = pts.map(function (p, i) {
      var x = PAD + ((new Date(p.t).getTime() - t0) / span) * (W - 2 * PAD);
      var y = H - PAD - ((p.v - v0) / vspan) * (H - 2 * PAD);
      return (i ? "L" : "M") + x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    var dots = pts.map(function (p) {
      var x = PAD + ((new Date(p.t).getTime() - t0) / span) * (W - 2 * PAD);
      var y = H - PAD - ((p.v - v0) / vspan) * (H - 2 * PAD);
      return '<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="3"><title>' + esc(p.t + " · " + p.kind + " · " + Math.round(p.v)) + "</title></circle>";
    }).join("");
    var first = pts[0], last = pts[pts.length - 1];
    var chg = last.v - first.v;
    var cls = chg > 0 ? "up" : chg < 0 ? "down" : "flat";
    return '<div class="chart-wrap"><svg viewBox="0 0 ' + W + " " + H + '" class="attention-svg" role="img" aria-label="' + esc(series.label) + ' history">' +
      '<path d="' + path + '" fill="none" stroke="currentColor" stroke-width="1.6" />' + dots + '</svg>' +
      '<div class="chart-meta"><span>' + esc(first.t + " → " + last.t) + '</span>' +
      '<span class="delta ' + cls + '">' + esc((chg > 0 ? "+" : "") + Math.round(chg)) + ' over window</span>' +
      '<span class="muted">' + esc(String(pts.length) + " observations") + '</span></div></div>';
  }

  function row(cells) {
    return "<tr>" + cells.map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>";
  }

  function fmt(v) {
    if (v == null || v === "") return '<span class="muted">—</span>';
    return esc(v);
  }

  /* ---- views ---------------------------------------------------------- */

  function viewTape() {
    setNav("tape");
    api("/api/tape?limit=100").then(function (items) {
      var html = "<h1>Activity Tape</h1><p class='sub'>What changed in live entertainment?</p>";
      if (!items || !items.length) {
        content.innerHTML = html + '<div class="none">No tape activity yet. Run the OA to derive tape entries from the warehouse.</div>';
        return;
      }
      html += "<h2>Recent changes</h2><table><thead><tr><th>Time</th><th>Entity</th><th>Change</th><th>Source</th></tr></thead><tbody>";
      items.forEach(function (t) {
        html += row([
          fmt(t.observed_at ? String(t.observed_at).slice(0, 19) : null),
          linkTo("events", t.entity_id, t.entity_id),
          '<span class="tape-type">' + esc(t.activity_type) + "</span>",
          esc(t.source_provider),
        ]);
      });
      html += "</tbody></table>";
      content.innerHTML = html;
    });
  }

  function viewStatus() {
    setNav("status");
    var html = "<h1>Status</h1><p class='sub'>Recently changed events — cancellations, onsales, prices, promoters.</p>";
    api("/api/status?limit=50").then(function (changes) {
      html += "<h2>Changes</h2>" + tableOrNone(changes, ["When", "Event", "Change", "Artist", "Market", "Source"], function (c) {
        return row([
          fmt(c.observed_at ? String(c.observed_at).slice(0, 19) : null),
          esc(c.entity_id),
          '<span class="tape-type">' + esc(c.activity_type) + "</span>",
          esc(c.artist_id || ""), esc(c.market_id || ""), esc(c.source_provider || ""),
        ]);
      });
      api("/api/events/live?limit=50").then(function (events) {
        html += "<h2>Live Ticketmaster events</h2>" + tableOrNone(events, ["Date", "Event", "Artist", "Venue", "City", "Status", "Price", "Promoter"], function (e) {
          var price = (e.price_min != null || e.price_max != null)
            ? (fmt(e.price_min) + "–" + fmt(e.price_max) + " " + esc(e.price_currency || "")) : "<span class='muted'>—</span>";
          return row([fmt(e.local_date), esc(e.event_name), esc(e.artist_name), esc(e.venue_name), esc(e.city), esc(e.event_status), price, esc(e.promoter || "")]);
        });
        content.innerHTML = html;
      });
    });
  }

  function viewNews() {
    setNav("news");
    api("/api/news?limit=100").then(function (items) {
      var html = "<h1>News</h1><p class='sub'>Metadata-only news mentions (GDELT) — headlines, domains, publication times. No article text is stored.</p>";
      if (!items || !items.length) {
        content.innerHTML = html + '<div class="none">No news mentions. Run the data-fabric OA (GDELT) to acquire metadata.</div>';
        return;
      }
      html += "<table><thead><tr><th>Published</th><th>Entity</th><th>Type</th><th>Title</th><th>Domain</th></tr></thead><tbody>";
      items.forEach(function (n) {
        html += row([
          fmt(n.publication_time ? String(n.publication_time).slice(0, 16) : null),
          esc(n.entity_name),
          '<span class="pill ok">' + esc(n.entity_type) + "</span>",
          '<a href="' + esc(n.article_url || "#") + '" target="_blank" rel="noopener">' + esc(n.title) + "</a>",
          esc(n.domain),
        ]);
      });
      content.innerHTML = html + "</tbody></table>";
    });
  }

  function viewAttention() {
    setNav("attention");
    api("/api/attention?limit=100").then(function (items) {
      var html = "<h1>Attention</h1><p class='sub'>Wikimedia pageviews per artist — an attention channel, never a demand score. Missing articles are excluded, not shown as zero.</p>";
      if (!items || !items.length) {
        content.innerHTML = html + '<div class="none">No attention series. Run the data-fabric OA (Wikimedia) to acquire pageviews.</div>';
        return;
      }
      html += "<table><thead><tr><th>Artist</th><th>Article</th><th>Metric</th><th>Obs.</th><th>Latest window</th><th>Total</th></tr></thead><tbody>";
      items.forEach(function (a) {
        var key = String(a.artist_key).replace(/^name::/, "");
        html += row([
          linkTo("artists", key, key),
          esc(a.article_title),
          esc(a.metric_kind),
          fmt(a.observations),
          fmt(a.latest_window),
          fmt(a.total_value) + " " + esc(a.value_unit || ""),
        ]);
      });
      content.innerHTML = html + "</tbody></table>";
    });
  }

  function viewSearch(q) {
    setNav("");
    api("/api/search?q=" + encodeURIComponent(q)).then(function (items) {
      var html = "<h1>Search</h1><p class='sub'>" + esc(q) + "</p>";
      if (!items || !items.length) {
        content.innerHTML = html + '<div class="none">No matches.</div>';
        return;
      }
      html += "<table><thead><tr><th>Type</th><th>Name</th></tr></thead><tbody>";
      items.forEach(function (r) {
        html += row([
          '<span class="pill ok">' + esc(r.entity_type) + "</span>",
          linkTo(r.entity_type.toLowerCase() + "s", r.entity_id, r.name),
        ]);
      });
      content.innerHTML = html + "</tbody></table>";
    });
  }

  /* ---- TALENT BUYER: ARTIST SECURITY ---------------------------------- */

  function securityItems(section) {
    if (Array.isArray(section)) return section;
    return section && Array.isArray(section.items) ? section.items : [];
  }

  function securityCell(value) {
    return value == null || value === ""
      ? '<span class="unknown">UNKNOWN</span>' : esc(value);
  }

  function securityStatus(section) {
    return section && section.status ? section.status : (securityItems(section).length ? "OBSERVED" : "UNKNOWN");
  }

  function securityReasons(value) {
    if (Array.isArray(value)) return value.join(", ");
    return value || "UNKNOWN";
  }

  function securityUrl(url, label) {
    if (!url) return securityCell(label);
    return '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(label || url) + '</a>';
  }

  function securitySourceLine(source) {
    if (!source) return '<span class="unknown">UNKNOWN</span>';
    var sourceName = source.source || source.provider || source.source_system || "UNKNOWN";
    var observed = source.observed_at || source.latest_observation || source.retrieved_at;
    var knowledge = source.knowledge_time || source.known_at;
    var freshness = source.freshness || source.freshness_status;
    return '<span class="security-source">' + esc(sourceName) + '</span>' +
      ' <span class="muted">obs ' + securityCell(observed ? String(observed).slice(0, 19) : null) +
      ' · knowledge ' + securityCell(knowledge ? String(knowledge).slice(0, 19) : null) +
      (freshness ? ' · ' + esc(freshness) : '') + '</span>';
  }

  function attentionPanel(title, source, opts) {
    var rows = securityItems(source);
    var observations = source && (source.observations || source.items) || rows;
    if (!Array.isArray(observations)) observations = [];
    var status = securityStatus(source);
    var o = opts || {};
    var chart = o.chart && source && source.series && source.series[0] ? attentionChart(source.series[0], o.windowDays) : null;
    var html = '<div class="security-card" id="attention-' + String(title).toLowerCase() + '"><div class="security-card-head"><h3>' + esc(title) +
      '</h3><span class="pill ' + (status === "OBSERVED" ? "ok" : "off") + '">' + esc(status) + '</span></div>';
    if (source && source.note) html += '<p class="muted">' + esc(source.note) + '</p>';
    if (chart) html += chart;
    if (!observations.length) {
      return html + compactEmpty("NO OBSERVATIONS", source && source.note ? "" : undefined, source && source.latest_knowledge_time) + '</div>';
    }
    html += '<table><thead><tr><th>Window</th><th>Latest observation</th><th>Value</th><th>Change</th><th>Evidence</th></tr></thead><tbody>';
    observations.slice(0, 24).forEach(function (obs) {
      var window = obs.observation_window || obs.window || ((obs.period_start || "") + (obs.period_end ? " → " + obs.period_end : ""));
      var latest = obs.latest_observation || obs.observed_at || obs.period_end || obs.retrieved_at;
      var value = obs.value != null ? obs.value : (obs.value_sum != null ? obs.value_sum : obs.listeners);
      html += row([securityCell(window), securityCell(latest ? String(latest).slice(0, 19) : null),
        securityCell(value == null ? null : String(value) + (obs.unit ? " " + obs.unit : "")),
        deltaChip(obs.change, obs.change_pct),
        securityUrl(obs.source_url || obs.url, obs.source_provider || obs.provider || obs.source_system || "source")]);
    });
    return html + '</tbody></table>' + (source && source.source ? '<p class="muted">' + securitySourceLine(source) + '</p>' : '') + '</div>';
  }

  function renderSecurityHistory(items, filter) {
    var selected = filter || "ALL";
    var filtered = items.filter(function (item) {
      var type = String(item.event_type || item.type || "").toUpperCase();
      if (selected === "ALL") return true;
      if (selected === "YEAR:") return true;
      if (selected.indexOf("YEAR:") === 0) return String(item.date || item.event_date || item.start_date || "").slice(0, 4) === selected.slice(5);
      if (selected === "MARKET") return !!(item.market || item.city);
      return type === selected || (selected === "FESTIVAL" && (item.festival || /festival/i.test(String(item.series || ""))));
    });
    if (!filtered.length) return '<div class="none">UNKNOWN — no matching live-history evidence.</div>';
    return '<table><thead><tr><th>Date</th><th>Event</th><th>Venue</th><th>Market</th><th>Type</th><th>Series / festival</th><th>Source</th></tr></thead><tbody>' +
      filtered.map(function (item) {
        var eid = item.event_id || item.event_key || item.id;
        var eventLabel = item.event || item.event_name || eid;
        var event = item.source_url || item.url
          ? securityUrl(item.source_url || item.url, eventLabel)
          : (eid ? linkTo("events", eid, eventLabel) : securityCell(eventLabel));
        return row([securityCell(item.date || item.event_date || item.start_date), event,
          securityCell(item.venue || item.venue_name), securityCell(item.market || item.city),
          securityCell(item.event_type || item.type), securityCell(item.series || item.festival || item.series_name),
          securityUrl(item.source_url || item.url, item.source || item.provider || item.source_system || "source")]);
      }).join("") + '</tbody></table>';
  }

  function viewArtistSecurity(id) {
    setNav("artists");
    content.innerHTML = '<div class="security-loading">Loading Artist Security…</div>';
    api("/api/artist-security/" + encodeURIComponent(id)).then(function (payload) {
      if (!payload || !payload.artist) {
        content.innerHTML = '<h1>ARTIST SECURITY</h1><div class="none">Artist security record not found.</div>';
        return;
      }
      var artist = payload.artist || {};
      var name = artist.name || artist.artist_name || artist.display_name || id;
      var artistKey = artist.artist_key || artist.key || id;
      var facts = payload.quick_facts || {};
      var identity = artist.primary_identity || artist.identity || {};
      var attention = payload.attention || {};
      var peers = payload.peers || {};
      var markets = payload.markets || {};
      var history = payload.history || {};
      var festivals = payload.festivals || {};
      var future = payload.future || {};
      var alternatives = payload.alternatives || {};
      var evidence = payload.evidence || {};
      var html = '<div class="security-kicker">' + esc(payload.release_label || "TALENT BUYER TERMINAL") +
        ' · ' + esc(payload.contract_version || "artist-security") + '</div>' +
        '<div class="security-header"><div><h1>' + esc(name) + '</h1><p class="sub">ARTIST SECURITY · evidence for underwriting research · no booking recommendation</p>' +
        '<p class="muted">Identity: ' + securityCell(identity.name || artistKey) +
        ' · ' + securityCell(identity.type || artist.type) + ' · ' + securityCell(identity.area || artist.area) + '</p></div>' +
        '<div class="security-header-meta"><span class="pill ok">READ ONLY</span><span class="pill">' + esc(artist.tier || "UNIVERSE") + '</span>' +
        '<div class="muted">freshness ' + securityCell(artist.freshness || payload.freshness) + '</div><div class="muted">coverage ' + securityCell(artist.evidence_coverage || payload.evidence_coverage) + '</div></div></div>';

      html += '<div class="security-actions panel"><label>Project <select id="security-project"><option value="">Choose existing project…</option></select></label> ' +
        '<button id="security-add-project" data-key="' + esc(artistKey) + '" data-name="' + esc(name) + '">ADD TO PROJECT</button> ' +
        '<button id="security-add-shortlist" data-key="' + esc(artistKey) + '" data-name="' + esc(name) + '">ADD TO SHORTLIST</button> ' +
        '<button id="security-underwrite">UNDERWRITE</button> ' +
        '<button id="security-compare" data-key="' + esc(artistKey) + '">COMPARE</button><span id="security-action-message" class="muted"></span></div>';

      var factKeys = [
        ["future_events", "Future events"], ["historical_live_events", "Historical live events"],
        ["festival_appearances", "Festival appearances"], ["markets_played", "Markets played"],
        ["venues_played", "Venues played"], ["active_ticket_evidence", "Active ticket evidence"],
        ["audience_affinity_available", "Audience affinity"]
      ];
      html += '<div class="security-facts">' + factKeys.map(function (pair) {
        return '<div class="security-fact"><div class="security-fact-label">' + esc(pair[1]) + '</div><div class="security-fact-value">' + securityCell(facts[pair[0]]) + '</div></div>';
      }).join("") + '</div>';

      /* ---- function sub-navigation (sticky, keyboard-adjacent) ------------ */
      var funcs = ["OVERVIEW", "ATTENTION", "MARKETS", "LIVE", "FESTIVALS", "AUDIENCE", "TICKETS", "EVIDENCE"];
      var funcTargets = { OVERVIEW: "sec-overview", ATTENTION: "sec-attention", MARKETS: "sec-markets", LIVE: "sec-live", FESTIVALS: "sec-festivals", AUDIENCE: "sec-peers", TICKETS: "sec-tickets", EVIDENCE: "sec-evidence" };
      html += '<nav class="subnav" id="artist-subnav">' + funcs.map(function (f) {
        return '<button data-anchor="' + funcTargets[f] + '">' + esc(f) + '</button>';
      }).join("") + '</nav>';

      /* ---- decision band: WHAT IS HAPPENING / WHAT IS NEXT / WHERE -------- */
      function latestOf(src) { var items = securityItems(src); return items && items.length ? items[0] : null; }
      var peerItemsAll = securityItems(peers);
      var marketItemsAll = securityItems(markets);
      var futureItemsAll = securityItems(future);
      var nextFuture = futureItemsAll.filter(function (f) { return f.event_date; }).sort(function (a, b) { return String(a.event_date).localeCompare(String(b.event_date)); })[0];
      var nextPrice = nextFuture && (nextFuture.price_min != null || nextFuture.price_max != null) ? String(nextFuture.price_min == null ? "?" : nextFuture.price_min) + "–" + String(nextFuture.price_max == null ? "?" : nextFuture.price_max) + " " + (nextFuture.currency || "") : null;
      function attentionCell(label, src) {
        var latest = latestOf(src);
        var status = securityStatus(src);
        if (!latest) return '<div class="dec-row"><span class="dec-k">' + esc(label) + '</span><span class="unknown">UNKNOWN</span></div>';
        var value = latest.value != null ? latest.value : (latest.value_sum != null ? latest.value_sum : latest.listeners);
        var unit = latest.unit ? " " + latest.unit : "";
        return '<div class="dec-row"><span class="dec-k">' + esc(label) + '</span><span class="dec-v">' + esc(String(value) + unit) + '</span>' + deltaChip(latest.change, latest.change_pct) + '</div>';
      }
      html += '<div class="dec-band" id="sec-overview">' +
        '<div class="dec-cell"><h4>ATTENTION NOW</h4>' +
          attentionCell("ListenBrainz", attention.listenbrainz) + attentionCell("Wikimedia", attention.wikimedia) + attentionCell("YouTube", attention.youtube) +
          '<p class="muted">attention is not demand</p></div>' +
        '<div class="dec-cell"><h4>WHAT IS NEXT</h4>' +
          (nextFuture
            ? '<div class="dec-row"><span class="dec-k">Next event</span><span class="dec-v">' + esc(String(nextFuture.event_date)) + '</span></div>' +
              '<div class="dec-row"><span class="dec-k">Venue</span>' + (nextFuture.venue_name || nextFuture.venue ? linkTo("venues", String(nextFuture.venue_name || nextFuture.venue).toLowerCase(), nextFuture.venue_name || nextFuture.venue) : '<span class="unknown">UNKNOWN</span>') + '</div>' +
              '<div class="dec-row"><span class="dec-k">Market</span>' + (nextFuture.market_name || nextFuture.market || nextFuture.city ? linkTo("markets", nextFuture.market_name || nextFuture.market || nextFuture.city, nextFuture.market_name || nextFuture.market || nextFuture.city) : '<span class="unknown">UNKNOWN</span>') + '</div>' +
              (nextPrice ? '<div class="dec-row"><span class="dec-k">Advertised</span><span class="dec-v">' + esc(nextPrice) + '</span></div>' : '<div class="dec-row"><span class="dec-k">Ticket evidence</span><span class="unknown">NONE</span></div>')
            : '<div class="dec-row"><span class="dec-k">Next event</span><span class="unknown">UNKNOWN</span></div>' +
              '<p class="muted">No retained forward observation in this serving generation.</p>') + '</div>' +
        '<div class="dec-cell"><h4>TOP MARKETS</h4>' +
          (marketItemsAll.length
            ? marketItemsAll.slice(0, 5).map(function (m) {
                var mkey = m.market_key || m.market || m.market_name;
                return '<div class="dec-row"><span class="dec-k">' +
                  linkTo("markets", mkey, m.market || m.market_name || mkey, { from: "artist", fromKey: artistKey, fromName: name }) + '</span>' +
                  '<span class="dec-v">' + esc(String(m.observed_shows || m.historical_shows || "?") + " shows") + '</span>' +
                  (m.future_events ? '<span class="pill ok">' + esc(String(m.future_events) + " upcoming") + '</span>' : "") + '</div>';
              }).join("")
            : '<span class="unknown">UNKNOWN</span>') + '</div>' +
        '</div>';

      /* ---- second band: peers + festival exposure ------------------------- */
      var topSeriesNames = [];
      securityItems(festivals).slice(0, 40).forEach(function (f) {
        var n = f.festival || f.series || f.festival_name || f.series_name;
        if (n && topSeriesNames.indexOf(n) < 0) topSeriesNames.push(n);
      });
      html += '<div class="dec-band dec-band-2">' +
        '<div class="dec-cell wide"><h4>TOP AUDIENCE PEERS</h4>' +
          (peerItemsAll.length
            ? peerItemsAll.slice(0, 5).map(function (p) {
                var pk = p.artist_key || p.key || p.id;
                var pn = p.artist_name || p.name || pk;
                return '<div class="dec-row"><span class="dec-k">' + (pk ? linkTo("artists", pk, pn) : esc(pn)) + '</span>' +
                  '<span class="dec-reason">' + esc(p.why_related || "") + '</span>' +
                  '<button class="mini" data-compare-with="' + esc(pk || "") + '">COMPARE</button></div>';
              }).join("")
            : compactEmpty("NO PEER EVIDENCE", "No ListenBrainz pilot edges for this artist.")) + '</div>' +
        '<div class="dec-cell"><h4>FESTIVAL EXPOSURE</h4>' +
          '<div class="dec-row"><span class="dec-k">Appearances</span><span class="dec-v">' + securityCell(facts.festival_appearances) + '</span></div>' +
          (topSeriesNames.length
            ? topSeriesNames.slice(0, 4).map(function (n) { return '<div class="dec-row"><span class="dec-reason">' + esc(n) + '</span></div>'; }).join("")
            : '<span class="unknown">UNKNOWN</span>') + '</div>' +
        '</div>';

      html += '<div class="security-grid" id="sec-attention"><section><h2>ATTENTION / MOMENTUM</h2><p class="sub">Source-separated attention evidence. Attention is not demand or ticket intent.</p><div class="panel attention-windows"><label>Window <select id="attention-window"><option value="">MAX</option><option value="365">1Y</option><option value="90">90D</option><option value="30">30D</option></select></label></div>' +
        '<div id="attention-panels">' + attentionPanel("Wikimedia", attention.wikimedia, { chart: true }) + attentionPanel("ListenBrainz", attention.listenbrainz, { chart: true }) + attentionPanel("YouTube", attention.youtube, { chart: true }) + '</div></section>';
      html += '<section id="sec-peers"><h2>AUDIENCE PEERS</h2><div class="security-card"><div class="security-card-head"><h3>' + esc(peers.label || "PILOT AUDIENCE DATA") + '</h3><span class="pill ' + (securityStatus(peers) === "OBSERVED" ? "ok" : "off") + '">' + esc(securityStatus(peers)) + '</span></div>';
      html += '<p class="muted">Shared listeners and Jaccard are descriptive affinity evidence. Pilot coverage remains labeled until a full-corpus replacement is published.</p>';
      var peerItems = peerItemsAll;
      if (!peerItems.length) html += compactEmpty("NO PEER EDGES", "No audience-affinity edges in this serving generation.", peers && peers.note);
      else html += '<table><thead><tr><th>Artist</th><th>Shared listeners</th><th>Jaccard</th><th>Why related</th><th>Differences</th><th></th></tr></thead><tbody>' + peerItems.slice(0, 25).map(function (p) {
        var peerKey = p.artist_key || p.key || p.id;
        var peerName = p.artist_name || p.name || peerKey;
        var reason = p.why_related || p.reason || securityReasons(p.reasons) || "shared audience";
        var difference = p.differences || p.difference || "UNKNOWN";
        return row([peerKey ? linkTo("artists", peerKey, peerName) : securityCell(peerName), securityCell(p.shared_listener_count != null ? p.shared_listener_count : p.shared_listeners), securityCell(p.jaccard), securityCell(reason), securityCell(difference),
          peerKey ? '<button class="mini" data-compare-with="' + esc(peerKey) + '">COMPARE</button>' : ""]);
      }).join("") + '</tbody></table>';
      html += '</div></section></div>';

      html += '<section id="sec-markets"><h2>MARKET PROFILE</h2><div class="panel"><label>Sort <select id="security-market-sort"><option value="activity">Observable activity</option><option value="last_played">Last played</option><option value="future">Future events</option><option value="market">Market</option></select></label></div><div id="security-market-table"></div></section>';
      html += '<section id="sec-live"><h2>LIVE HISTORY</h2><div class="panel security-filters"><label>Filter <select id="security-history-filter"><option>ALL</option><option>FESTIVAL</option><option>CONCERT</option><option>MARKET</option><option>YEAR</option></select></label> <label>Year <select id="security-history-year"><option value="ALL">All years</option></select></label></div><div id="security-history-table"></div></section>';
      html += '<section id="sec-festivals"><h2>FESTIVAL HISTORY</h2><div class="security-card"><p class="sub">Observed festival/series appearances and co-billing; repeat count remains descriptive.</p>' + (securityItems(festivals).length ? tableOrNone(securityItems(festivals), ["Festival / series", "Year", "Event", "Market", "Venue / site", "Co-billed artists", "Repeat"], function (f) {
        return row([securityCell(f.festival || f.series || f.festival_name || f.series_name), securityCell(f.year || f.edition_year), securityCell(f.event || f.event_name), securityCell(f.market || f.market_name || f.city), securityCell(f.venue || f.site || f.venue_name), securityCell(Array.isArray(f.co_billed_artists) ? f.co_billed_artists.join(", ") : (f.co_billed_artists || f.cobilled || f.co_billed_artist_names)), securityCell(f.repeat_appearances || f.repeat_count || f.repeat_appearance_count)]);
      }) : compactEmpty("NO FESTIVAL APPEARANCES", "No festival/series evidence for this artist.", festivals && festivals.note)) + '</div></section>';
      html += '<section id="sec-tickets"><h2>FORWARD EVENTS / TICKET EVIDENCE</h2><div class="security-card"><p class="strict-note"><strong>Ticketmaster priceRange = advertised structured price evidence.</strong> It is not transaction price, resale execution, attendance, or ticket sales.</p>' +
        (securityItems(future).length ? tableOrNone(securityItems(future), ["Date", "Venue", "Market", "Provider", "Advertised price range", "Latest observation"], function (f) {
          var price = f.price_range || ((f.price_min != null || f.price_max != null) ? String(f.price_min == null ? "UNKNOWN" : f.price_min) + "–" + String(f.price_max == null ? "UNKNOWN" : f.price_max) + " " + (f.currency || "") : null);
          return row([securityCell(f.date || f.event_date), securityCell(f.venue || f.venue_name), securityCell(f.market || f.market_name || f.city), securityCell(f.provider || f.source || f.source_system), securityCell(price), securityCell(f.latest_observation || f.observed_at || f.retrieved_at)]);
        }) : compactEmpty("NO CURRENT TICKET EVIDENCE", "No advertised structured price evidence in this serving generation.", future && future.ticket_evidence_status)) + '</div></section>';

      html += '<div class="security-grid"><section><h2>EXPLAINABLE ALTERNATIVES</h2><div class="security-card"><p class="sub">Related artists are shown with reasons and observable differences, never fixed model weights.</p>' + (securityItems(alternatives).length ? tableOrNone(securityItems(alternatives), ["Artist", "Why related", "Differences", "Evidence"], function (alt) {
        var altKey = alt.artist_key || alt.key || alt.id;
        return row([altKey ? linkTo("artists", altKey, alt.artist_name || alt.name || altKey) : securityCell(alt.artist_name || alt.name), securityCell(alt.why_related || alt.reason || securityReasons(alt.reasons)), securityCell(alt.differences || alt.difference), securityCell(alt.evidence || alt.source || alt.source_system)]);
      }) : compactEmpty("NO ALTERNATIVE EVIDENCE", "No explainable alternative evidence for this artist.")) + '</div></section>';
      html += '<section id="sec-evidence"><h2>EVIDENCE / FRESHNESS</h2><div class="security-card">' + (securityItems(evidence).length ? tableOrNone(securityItems(evidence), ["Panel", "Source", "Observed", "Knowledge", "Freshness", "Status"], function (e) {
        return row([securityCell(e.panel || e.scope), securityUrl(e.source_url || e.url, e.source || e.provider || e.source_system || "source"), securityCell(e.observed_at || e.observation_time), securityCell(e.knowledge_time || e.known_at), securityCell(e.freshness || e.freshness_status), securityCell(e.status)]);
      }) : compactEmpty("NO EVIDENCE LEDGER", "No evidence ledger rows in this serving generation.")) + '<p class="muted">UNKNOWN remains distinct from zero. Source conflicts are retained as separate evidence.</p></div></section></div>';

      content.innerHTML = html;

      pushRecent("artist", artistKey, name);

      /* ---- artist-page wiring ----------------------------------------- */
      document.querySelectorAll("#artist-subnav [data-anchor]").forEach(function (b) {
        b.onclick = function () {
          var el = document.getElementById(b.getAttribute("data-anchor"));
          if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        };
      });
      document.querySelectorAll("[data-compare-with]").forEach(function (b) {
        b.onclick = function () {
          var other = b.getAttribute("data-compare-with");
          if (other) location.hash = "#/compare-security/" + encodeURIComponent(artistKey) + "/" + encodeURIComponent(other);
        };
      });
      var winSel = document.getElementById("attention-window");
      if (winSel) winSel.onchange = function () {
        var days = winSel.value ? Number(winSel.value) : null;
        document.getElementById("attention-panels").innerHTML =
          attentionPanel("Wikimedia", attention.wikimedia, { chart: true, windowDays: days }) +
          attentionPanel("ListenBrainz", attention.listenbrainz, { chart: true, windowDays: days }) +
          attentionPanel("YouTube", attention.youtube, { chart: true, windowDays: days });
      };

      var projectSelect = document.getElementById("security-project");
      api("/api/planning/projects").then(function (projects) {
        (projects || []).forEach(function (project) {
          var option = document.createElement("option"); option.value = project.project_key; option.textContent = project.name; projectSelect.appendChild(option);
        });
      });
      var message = document.getElementById("security-action-message");
      function selectedProject() { return projectSelect && projectSelect.value; }
      function actionMessage(text) { if (message) message.textContent = text; }
      document.getElementById("security-add-project").onclick = function () {
        var project = selectedProject(); if (!project) { actionMessage("Choose a project first."); return; }
        api("/api/planning/projects/" + encodeURIComponent(project) + "/candidates", { method: "POST", body: JSON.stringify({ artist_key: artistKey, artist_name: name }) }).then(function () { actionMessage("Added to project."); });
      };
      document.getElementById("security-add-shortlist").onclick = function () {
        var project = selectedProject(); if (!project) { actionMessage("Choose a project first."); return; }
        api("/api/planning/projects/" + encodeURIComponent(project) + "/shortlist", { method: "POST", body: JSON.stringify({ artist_key: artistKey, artist_name: name, status: "SHORTLIST" }) }).then(function () { actionMessage("Added to shortlist."); });
      };
      document.getElementById("security-underwrite").onclick = function () {
        var project = selectedProject(); if (!project) { actionMessage("Choose a project first."); return; }
        location.hash = "#/build/" + encodeURIComponent(project) + "/economics/" + encodeURIComponent(artistKey);
      };
      document.getElementById("security-compare").onclick = function () {
        var peerInput = document.getElementById("security-compare-artist");
        if (peerInput && peerInput.value.trim()) {
          api("/api/search?q=" + encodeURIComponent(peerInput.value.trim()) + "&limit=5").then(function (hits) {
            var hit = (hits || []).filter(function (h) { return h.entity_type === "ARTIST"; })[0];
            if (hit) location.hash = "#/compare-security/" + encodeURIComponent(artistKey) + "/" + encodeURIComponent(hit.entity_id);
            else actionMessage("No artist match found.");
          });
        } else actionMessage("Enter an artist below to compare.");
      };
      var compareButton = document.getElementById("security-compare");
      compareButton.insertAdjacentHTML("afterend", '<input id="security-compare-artist" placeholder="artist to compare…" aria-label="Artist to compare" />');

      var marketItems = securityItems(markets);
      function renderMarkets() {
        var sort = document.getElementById("security-market-sort").value;
        var rows = marketItems.slice().sort(function (left, right) {
          if (sort === "market") return String(left.market || left.market_name || "").localeCompare(String(right.market || right.market_name || ""));
          if (sort === "last_played") return String(right.last_played || right.last_played_date || "").localeCompare(String(left.last_played || left.last_played_date || ""));
          if (sort === "future") return (Number(right.future_events) || 0) - (Number(left.future_events) || 0);
          return ((Number(right.historical_shows) || 0) + (Number(right.festival_appearances) || 0) + (Number(right.venues_played) || 0)) - ((Number(left.historical_shows) || 0) + (Number(left.festival_appearances) || 0) + (Number(left.venues_played) || 0));
        });
        document.getElementById("security-market-table").innerHTML = tableOrNone(rows, ["Market", "Historical shows", "Venues", "Last played", "Future events", "Next event", "Ticket evidence"], function (m) {
          var mkey = m.market_key || m.market || m.market_name;
          var next = m.next_event ? (m.next_event.date || "") + (m.next_event.venue ? " · " + m.next_event.venue : "") : null;
          return row([linkTo("markets", mkey, m.market || m.market_name || mkey, { from: "artist", fromKey: artistKey, fromName: name }), securityCell(m.historical_shows), securityCell(m.venues_played), securityCell(m.last_played || m.last_played_date), securityCell(m.future_events), securityCell(next), securityCell(m.ticket_evidence_available != null ? m.ticket_evidence_available : m.ticket_evidence)]);
        });
      }
      document.getElementById("security-market-sort").onchange = renderMarkets; renderMarkets();
      var historyItems = securityItems(history);
      var yearSelect = document.getElementById("security-history-year");
      Array.from(new Set(historyItems.map(function (item) { return String(item.date || item.event_date || item.start_date || "").slice(0, 4); }).filter(function (year) { return /^\d{4}$/.test(year); }))).sort().reverse().forEach(function (year) { yearSelect.insertAdjacentHTML("beforeend", '<option value="' + esc(year) + '">' + esc(year) + '</option>'); });
      function renderHistory() { var year = yearSelect.value; var kind = document.getElementById("security-history-filter").value; var selected = kind === "YEAR" && year !== "ALL" ? "YEAR:" + year : (kind === "YEAR" ? "ALL" : (year === "ALL" ? kind : "YEAR:" + year)); document.getElementById("security-history-table").innerHTML = renderSecurityHistory(historyItems, selected); }
      document.getElementById("security-history-filter").onchange = renderHistory; yearSelect.onchange = renderHistory; renderHistory();
    });
  }

  function securityCompareValue(value) {
    if (value == null) return null;
    if (Array.isArray(value)) {
      if (!value.length) return "UNKNOWN";
      return value.map(function (item) {
        if (item && typeof item === "object") {
          var market = item.market_key || item.market || item.name || "evidence";
          var count = item.historical_shows != null ? " · " + item.historical_shows + " observed shows" : "";
          var last = item.last_play ? " · last " + item.last_play : "";
          return market + count + last;
        }
        return String(item);
      }).join("; ");
    }
    if (typeof value === "object") {
      return Object.keys(value).map(function (key) {
        var item = value[key];
        return key.replace(/_/g, " ") + ": " + (item == null || item === "" ? "UNKNOWN" : String(item));
      }).join("; ");
    }
    return value;
  }

  function viewSecurityCompare(a, b) {
    setNav("compare");
    pushRecent("compare", a + "|" + b, "compare");
    content.innerHTML = '<h1>COMPARE</h1><p class="sub">Side-by-side artist evidence. No winner, no opaque score, no automated booking advice.</p><div class="security-loading">Loading comparison…</div>';
    api("/api/artist-security/compare?a=" + encodeURIComponent(a) + "&b=" + encodeURIComponent(b)).then(function (comparison) {
      if (!comparison) { content.innerHTML += '<div class="none">Comparison unavailable.</div>'; return; }
      var left = comparison.left || {}, right = comparison.right || {};
      var dims = Array.isArray(comparison.dimensions) ? comparison.dimensions : Object.keys(comparison.dimensions || {}).map(function (key) {
        var value = comparison.dimensions[key];
        return typeof value === "object" && value !== null ? Object.assign({ label: key }, value) : { label: key, left: value };
      });
      var byLabel = {};
      dims.forEach(function (d) { byLabel[d.label || d.dimension || d.name] = d; });
      var nameL = left.name || left.artist_name || a;
      var nameR = right.name || right.artist_name || b;

      function dimCell(label, fmt) {
        var d = byLabel[label];
        if (!d) return '<div class="dec-row"><span class="dec-k">' + esc(label) + '</span><span class="unknown">UNKNOWN</span></div>';
        var lv = d.left != null ? d.left : (d.a != null ? d.a : (d.values || [])[0]);
        var rv = d.right != null ? d.right : (d.b != null ? d.b : (d.values || [])[1]);
        var lvS = fmt ? fmt(lv) : securityCompareValue(lv);
        var rvS = fmt ? fmt(rv) : securityCompareValue(rv);
        var differs = lvS != null && rvS != null && String(lvS) !== String(rvS);
        return '<div class="dec-row' + (differs ? ' differs' : '') + '"><span class="dec-k">' + esc(label) + '</span>' +
          '<span class="dec-v">' + (lvS == null ? '<span class="unknown">UNKNOWN</span>' : esc(lvS)) + '</span>' +
          '<span class="dec-v">' + (rvS == null ? '<span class="unknown">UNKNOWN</span>' : esc(rvS)) + '</span></div>';
      }
      function marketOverlapSection() {
        var lm = (left.strongest_markets || []).map(function (m) { return m.market_key; });
        var rm = (right.strongest_markets || []).map(function (m) { return m.market_key; });
        var shared = lm.filter(function (k) { return rm.indexOf(k) >= 0; });
        var lOnly = lm.filter(function (k) { return rm.indexOf(k) < 0; });
        var rOnly = rm.filter(function (k) { return lm.indexOf(k) < 0; });
        return '<div class="dec-row' + (lOnly.length || rOnly.length ? ' differs' : '') + '"><span class="dec-k">Market overlap</span>' +
          '<span class="dec-v">' + shared.length + " shared · " + lOnly.length + " only-" + esc(nameL) + '</span>' +
          '<span class="dec-v">' + shared.length + " shared · " + rOnly.length + " only-" + esc(nameR) + '</span></div>' +
          (lOnly.length ? '<div class="dec-row"><span class="dec-k">Only ' + esc(nameL) + '</span><span class="dec-reason">' + esc(lOnly.slice(0, 6).join(" · ")) + '</span></div>' : "") +
          (rOnly.length ? '<div class="dec-row"><span class="dec-k">Only ' + esc(nameR) + '</span><span class="dec-reason">' + esc(rOnly.slice(0, 6).join(" · ")) + '</span></div>' : "");
      }
      var sections = [
        { id: "cmp-audience", title: "AUDIENCE", rows: [dimCell("Audience peers"), '<div class="dec-row"><span class="dec-k">Shared-audience evidence</span><span class="dec-reason">ListenBrainz 1% pilot peer edges; shared listeners, not ticket intent.</span></div>'] },
        { id: "cmp-attention", title: "ATTENTION", rows: [dimCell("Attention sources"), dimCell("Identity"), dimCell("Evidence coverage")] },
        { id: "cmp-live", title: "LIVE", rows: [dimCell("Live history")] },
        { id: "cmp-markets", title: "MARKETS", rows: [marketOverlapSection(), dimCell("Strongest observed markets")] },
        { id: "cmp-festivals", title: "FESTIVALS", rows: [dimCell("Festival history")] },
        { id: "cmp-forward", title: "FORWARD", rows: [dimCell("Future events")] },
        { id: "cmp-tickets", title: "TICKETS", rows: [dimCell("Current ticket ranges")] }
      ];
      var html = '<h1>COMPARE</h1><p class="sub">A/B evidence review · ' + esc(comparison.no_winner === false ? "review only" : "no winner") + '</p>';
      html += '<div class="security-compare-head"><div><strong>' + esc(nameL) + '</strong></div><div><strong>' + esc(nameR) + '</strong></div></div>';
      html += '<div class="compare-actions panel">' +
        '<button id="cmp-swap">SWAP</button>' +
        '<input id="cmp-new" placeholder="change comparator…" aria-label="Change comparator" />' +
        '<button id="cmp-go">CHANGE</button>' +
        '<button data-shortlist="a" data-key="' + esc(a) + '" data-name="' + esc(nameL) + '">ADD ' + esc(nameL.toUpperCase()) + ' TO SHORTLIST</button>' +
        '<button data-shortlist="b" data-key="' + esc(b) + '" data-name="' + esc(nameR) + '">ADD ' + esc(nameR.toUpperCase()) + ' TO SHORTLIST</button>' +
        '<select id="cmp-project"><option value="">Project…</option></select></div>';
      sections.forEach(function (sec) {
        html += '<section class="cmp-section" id="' + sec.id + '"><h3>' + esc(sec.title) + '</h3><div class="cmp-cols"><span class="cmp-col-l">' + esc(nameL) + '</span><span class="cmp-col-r">' + esc(nameR) + '</span></div>' + sec.rows.filter(Boolean).join("") + '</section>';
      });
      content.innerHTML = html + '<p><a href="#/artists/' + encodeURIComponent(a) + '">Open ' + esc(nameL) + ' Security</a> · <a href="#/artists/' + encodeURIComponent(b) + '">Open ' + esc(nameR) + ' Security</a></p>';

      document.getElementById("cmp-swap").onclick = function () {
        location.hash = "#/compare-security/" + encodeURIComponent(b) + "/" + encodeURIComponent(a);
      };
      document.getElementById("cmp-go").onclick = function () {
        var q = document.getElementById("cmp-new").value.trim();
        if (!q) return;
        api("/api/search?q=" + encodeURIComponent(q) + "&limit=5").then(function (hits) {
          var hit = (hits || []).filter(function (h) { return h.entity_type === "ARTIST"; })[0];
          if (hit) location.hash = "#/compare-security/" + encodeURIComponent(a) + "/" + encodeURIComponent(hit.entity_id);
        });
      };
      api("/api/planning/projects").then(function (projects) {
        var sel = document.getElementById("cmp-project");
        (projects || []).forEach(function (p) {
          var opt = document.createElement("option"); opt.value = p.project_key; opt.textContent = p.name; sel.appendChild(opt);
        });
      });
      document.querySelectorAll("[data-shortlist]").forEach(function (btn) {
        btn.onclick = function () {
          var project = document.getElementById("cmp-project").value;
          if (!project) { btn.textContent = "Choose a project first"; return; }
          api("/api/planning/projects/" + encodeURIComponent(project) + "/shortlist", {
            method: "POST", body: JSON.stringify({ artist_key: btn.getAttribute("data-key"), artist_name: btn.getAttribute("data-name"), status: "SHORTLIST" })
          }).then(function () { btn.textContent = "Added ✓"; });
        };
      });
    });
  }

  function viewArtist(id) {
    setNav("artists");
    api("/api/artists/" + encodeURIComponent(id)).then(function (a) {
      if (!a) { content.innerHTML = "<h1>Artist</h1><div class='none'>Not found.</div>"; return; }
      var html = "<h1>" + esc(a.name) + "</h1><p class='sub'>ARTIST · " +
        a.history_count + " historical · " + a.upcoming_count + " upcoming";
      if (a.spotify_id) { html += " · <a href='https://open.spotify.com/artist/" + esc(a.spotify_id) + "' target='_blank' rel='noopener'>Spotify</a>"; }
      html += "</p>";
      var wlKey = a.canonical && a.canonical.artist_key ? a.canonical.artist_key : id;
      html += "<div class='panel'><h3>Watchlist</h3><select id='art-wl-select'></select> " +
        "<button id='art-wl-add' data-key=\"" + esc(wlKey) + "\" data-name=\"" + esc(a.name || "") + "\">Add to watchlist</button></div>";
      if (a.canonical) {
        var c = a.canonical;
        html += "<div class='card'><h3>Identity</h3><table>" +
          row(["Type", esc(c.type || "")]) +
          row(["Area", esc(c.area || "")]) +
          row(["MBID", esc(c.musicbrainz_id || "")]) +
          row(["ISNI", esc(c.isni || "")]) +
          row(["IPI", esc(c.ipi || "")]) +
          row(["Sort name", esc(c.sort_name || "")]) +
          row(["Life span", esc((c.life_span_begin || "") + " → " + (c.life_span_end || ""))]) +
          (c.disambiguation ? row(["Disambiguation", esc(c.disambiguation)]) : "") +
          "</table></div>";
      }
      if (a.external_ids && a.external_ids.length) {
        var seen = {};
        html += "<h2>External identities</h2><table><thead><tr><th>Type</th><th>ID</th><th>Source</th></tr></thead><tbody>";
        a.external_ids.forEach(function (x) {
          if (seen[x.id_type] && seen[x.id_type] > 2) return;
          seen[x.id_type] = (seen[x.id_type] || 0) + 1;
          var cell = x.url ? '<a href="' + esc(x.url) + '" target="_blank" rel="noopener">' + esc(x.id_value || x.url) + "</a>" : esc(x.id_value || "");
          html += row([esc(x.id_type), cell, esc(x.source_system || x.namespace || "")]);
        });
        html += "</tbody></table>";
      }
      html += "<h2>Upcoming events</h2>" + tableOrNone(a.upcoming, ["Date", "Venue", "Market", "Status"], function (e) {
        return row([fmt(e.event_date), esc(e.venue_name), fmt(e.market), fmt(e.event_status)]);
      });
      html += "<h2>Historical performances</h2>" + tableOrNone(a.history, ["Date", "Venue", "Market", "Shows"], function (e) {
        return row([fmt(e.start_date), esc(e.venue), fmt(e.city), fmt(e.number_of_shows)]);
      });
      html += "<h2>Box-office outcomes</h2>" + tableOrNone(a.outcomes, ["Date", "Venue", "Headcount", "Gross", "Source"], function (o) {
        return row([fmt(o.start_date), esc(o.venue), fmt(o.headcount_total), fmt(o.ticket_gross_total), esc(o.reporting_source)]);
      });
      api("/api/artists/" + encodeURIComponent(id) + "/billing").then(function (bill) {
        var el = document.getElementById("artist-billing");
        if (!el) return;
        el.innerHTML = "<h2>Festival billing trajectory</h2>" + tableOrNone(bill, ["Year", "Festival", "Tier", "Context", "Evidence"], function (b) {
          return row([fmt(b.year), esc(b.festival_name), fmt(b.printed_tier), esc(b.billing_context), esc(b.evidence_class)]);
        });
      });
      html += "<div id='artist-billing'></div>";
      html += "<h2>Attention</h2>" + tableOrNone(a.attention, ["Date", "Value", "Unit", "Provider"], function (x) {
        return row([fmt(x.observed_date), fmt(x.value), fmt(x.unit), esc(x.provider)]);
      });
      html += "<h2>News</h2>" + tableOrNone(a.news, ["Title", "Domain", "Published"], function (n) {
        return row([esc(n.title), esc(n.domain), fmt(n.publication_time)]);
      });
      content.innerHTML = html;
      api("/api/watchlists").then(function (lists) {
        var sel = document.getElementById("art-wl-select");
        if (!sel) return;
        (lists || []).forEach(function (w) {
          var opt = document.createElement("option");
          opt.value = w.watchlist_key;
          opt.textContent = w.name;
          sel.appendChild(opt);
        });
        var btn = document.getElementById("art-wl-add");
        if (btn) btn.addEventListener("click", function () {
          if (!sel.value) return;
          api("/api/watchlists/" + encodeURIComponent(sel.value), { method: "POST", body: JSON.stringify({
            action: "add", entity_type: "ARTIST",
            entity_key: btn.getAttribute("data-key"),
            entity_name: btn.getAttribute("data-name") }) })
            .then(function () { content.innerHTML = "<div class='none'>Added. Watchlists refresh below — open the Watchlists view.</div>"; });
        });
      });
    });
  }

  function viewEvent(id) {
    setNav("events");
    pushRecent("event", id, id);
    api("/api/events/" + encodeURIComponent(id)).then(function (e) {
      if (!e) { content.innerHTML = "<h1>Event</h1><div class='none'>Not found.</div>"; return; }
      var html = "<h1>" + esc(e.artist_name || e.artist || id) + "</h1>" +
        "<p class='sub'>EVENT · " + esc(e.kind || "") + " · " + fmt(e.event_date || e.start_date) + "</p>";
      html += "<div class='card'><h3>Details</h3><table>" +
        row(["Venue", esc(e.venue_name || e.venue || "")]) +
        row(["Market", esc(e.market || e.city || "")]) +
        row(["Status", esc(e.event_status || e.tracking_status || "")]) +
        row(["Source", esc(e.provider || "")]) +
        "</table></div>";
      if (e.observations) {
        html += "<h2>Observations</h2>" + tableOrNone(e.observations, ["Milestone", "Status", "Price min", "Price max", "Observed"], function (o) {
          return row([esc(o.milestone), esc(o.event_status), fmt(o.price_min), fmt(o.price_max), fmt(o.observed_at)]);
        });
      }
      html += "<h2>Timeline</h2>" + tableOrNone(e.timeline, ["Cutoff", "Kind", "Evidence", "Source"], function (t) {
        return row([esc(t.cutoff_type), esc(t.cutoff_kind), esc(t.evidence_class), esc(t.source_provider)]);
      });
      html += "<h2>Competition (±7 days)</h2>" + tableOrNone(e.competition, ["Date", "Artist", "Venue"], function (c) {
        return row([fmt(c.start_date), esc(c.artist), esc(c.venue)]);
      });
      if (e.outcomes) {
        html += "<h2>Outcomes</h2>" + tableOrNone(e.outcomes, ["Outcome", "Value", "Unit", "Source"], function (o) {
          return row([esc(o.outcome_type), fmt(o.value), fmt(o.unit), esc(o.source_provider)]);
        });
      }
      content.innerHTML = html;
    });
  }

  function viewVenue(id) {
    setNav("venues");
    pushRecent("venue", id, id);
    api("/api/venues/" + encodeURIComponent(id)).then(function (v) {
      if (!v) { content.innerHTML = "<h1>Venue</h1><div class='none'>Not found.</div>"; return; }
      var html = "<h1>" + esc(v.name) + "</h1><p class='sub'>VENUE · " +
        v.history_count + " historical · " + v.upcoming_count + " upcoming</p>";
      html += "<h2>Capacity claims</h2>" + tableOrNone(v.capacity_claims, ["Venue", "Capacity", "Type", "Source"], function (c) {
        return row([esc(c.venue_name), fmt(c.capacity), esc(c.capacity_type), esc(c.source_provider)]);
      });
      html += "<h2>Upcoming</h2>" + tableOrNone(v.upcoming, ["Date", "Artist", "Status"], function (e) {
        return row([fmt(e.event_date), esc(e.artist_name), esc(e.event_status)]);
      });
      html += "<h2>History</h2>" + tableOrNone(v.history, ["Date", "Artist", "Market", "Shows"], function (e) {
        return row([fmt(e.start_date), esc(e.artist), fmt(e.city), fmt(e.number_of_shows)]);
      });
      content.innerHTML = html;
    });
  }

  function viewMarket(id) {
    setNav("markets");
    pushRecent("market", id, id);
    var context = null;
    try { context = JSON.parse(sessionStorage.getItem("fi.context") || "null"); } catch (e) { /* none */ }
    api("/api/markets/" + encodeURIComponent(id)).then(function (m) {
      if (!m) { content.innerHTML = "<h1>Market</h1><div class='none'>Not found.</div>"; return; }
      var html = "<h1>" + esc(m.name) + "</h1><p class='sub'>MARKET · " +
        m.upcoming_count + " upcoming · " + m.history_count + " historical</p>";
      if (context && context.from === "artist" && context.fromKey) {
        html += "<div class='context-bar'><span class='muted'>Viewing in context of</span> <strong>" + esc(context.fromName || context.fromKey) + "</strong> " +
          "<button class='mini' data-nav='artists' data-id='" + esc(context.fromKey) + "'>BACK TO " + esc(String(context.fromName || context.fromKey).toUpperCase()) + "</button> " +
          "<button class='mini' data-compare-with='" + esc(context.fromKey) + "' data-market='" + esc(id) + "'>COMPARE</button></div>";
      }
      html += "<h2>Upcoming events</h2>" + tableOrNone(m.upcoming, ["Date", "Artist", "Venue", "Status"], function (e) {
        var artistId = e.artist_key || e.artist_id || e.artist_mbid;
        return row([fmt(e.event_date), artistId ? linkTo("artists", artistId, e.artist_name) : esc(e.artist_name), e.venue_name ? linkTo("venues", String(e.venue_name).toLowerCase(), e.venue_name) : esc(e.venue_name), esc(e.event_status)]);
      });
      html += "<h2>Major venues</h2>" + (m.venues && m.venues.length
        ? "<ul>" + m.venues.map(function (v) { return "<li>" + linkTo("venues", v.toLowerCase(), v) + "</li>"; }).join("") + "</ul>"
        : '<div class="none">No venues recorded.</div>');
      html += "<h2>Context series</h2>" + tableOrNone(m.context, ["Type", "Date", "Value", "Vintage"], function (c) {
        return row([esc(c.series_type), fmt(c.observed_date), fmt(c.value), fmt(c.vintage)]);
      });
      content.innerHTML = html;
    });
  }

  function viewFestivals() {
    setNav("festivals");
    api("/api/festivals").then(function (items) {
      var html = "<h1>Festivals</h1><p class='sub'>Canonical festival spine — editions, lineups, billing observations.</p>";
      if (!items || !items.length) {
        content.innerHTML = html + '<div class="none">No festival corpus. Run the data-estate OA to seed the spine.</div>';
        return;
      }
      html += "<table><thead><tr><th>Festival</th><th>Location</th><th>First year</th><th>Source</th></tr></thead><tbody>";
      items.forEach(function (f) {
        html += row([
          linkTo("festivals", f.festival_key, f.name),
          esc(f.location_city || f.location_country || ""),
          fmt(f.first_edition_year),
          esc(f.source_system),
        ]);
      });
      content.innerHTML = html + "</tbody></table>";
    });
  }

  function viewFestival(id) {
    setNav("festivals");
    pushRecent("festival", id, id);
    api("/api/festivals/" + encodeURIComponent(id)).then(function (f) {
      if (!f) { content.innerHTML = "<h1>Festival</h1><div class='none'>Not found.</div>"; return; }
      var html = "<h1>" + esc(f.name) + "</h1><p class='sub'>FESTIVAL · " +
        esc(f.location_city || "") + ", " + esc(f.location_country || "") +
        " · first known year " + fmt(f.first_edition_year) + "</p>";
      (f.editions || []).forEach(function (ed) {
        html += "<h2>" + esc(ed.edition_key) + "</h2>" +
          "<p class='sub'>" + fmt(ed.start_date || ed.year) + " · " + esc(ed.venue_name || "") +
          " · precision " + esc(ed.date_precision) + "</p>";
        var lineup = ed.lineup || [];
        var billing = ed.billing || [];
        html += "<h3>Lineup (" + lineup.length + " acts) — " +
          esc((billing[0] && billing[0].evidence_class) || "") + "</h3>";
        var tierMap = {};
        billing.forEach(function (b) {
          (tierMap[b.printed_tier] = tierMap[b.printed_tier] || []).push(b);
        });
        var tierOrder = Object.keys(tierMap).sort();
        tierOrder.forEach(function (tier) {
          html += "<h4>Tier " + esc(tier) + "</h4><ul>";
          tierMap[tier].forEach(function (b) {
            html += "<li>" + esc(b.raw_artist_name) +
              " <span class='muted'>" + esc(b.billing_group || "") + "</span>" +
              " <a class='muted' href='" + esc(b.source_url || "#") + "' target='_blank' rel='noopener'>src</a></li>";
          });
          html += "</ul>";
        });
      });
      content.innerHTML = html;
    });
  }

  function viewToday() {
    setNav("today");
    content.innerHTML = '<div class="security-loading">Loading monitor…</div>';
    Promise.all([api("/api/monitor"), api("/api/today"), api("/api/planning/projects")]).then(function (all) {
      var mon = all[0] || {}, t = all[1] || {}, projects = all[2] || [];
      var recents = getRecents();
      var html = "<h1>TODAY</h1><p class='sub'>Buyer monitor · what changed, what is next, what you were working on.</p>";

      /* ---- WATCHLIST / SHORTLIST -------------------------------------- */
      var watched = mon.watched || [], shortlisted = mon.shortlisted || [];
      html += '<div class="dec-band"><div class="dec-cell"><h4>WATCHLIST (' + watched.length + ')</h4>' +
        (watched.length ? watched.slice(0, 8).map(function (w) {
          return '<div class="dec-row"><span class="dec-k">' + (w.artist_key ? linkTo("artists", w.artist_key, w.artist_name || w.artist_key) : esc(w.artist_name)) + '</span><span class="dec-reason">' + esc(w.watchlist || "") + '</span></div>';
        }).join("") : compactEmpty("NO WATCHLIST ITEMS", "Add artists to a watchlist to track them here.")) + '</div>';
      html += '<div class="dec-cell"><h4>SHORTLIST (' + shortlisted.length + ')</h4>' +
        (shortlisted.length ? shortlisted.slice(0, 8).map(function (s) {
          return '<div class="dec-row"><span class="dec-k">' + (s.artist_key ? linkTo("artists", s.artist_key, s.artist_name || s.artist_key) : esc(s.artist_name)) + '</span><span class="dec-reason">' + esc(s.project || "") + '</span></div>';
        }).join("") : compactEmpty("NO SHORTLIST ITEMS", "Shortlist artists from any Artist Security page.")) + '</div>';
      html += '<div class="dec-cell"><h4>PROJECTS (' + projects.length + ')</h4>' +
        (projects.length ? projects.slice(0, 6).map(function (p) {
          return '<div class="dec-row"><span class="dec-k">' + linkTo("build", p.project_key, p.name) + '</span></div>';
        }).join("") : compactEmpty("NO PROJECTS", "Create a project in BUILD.")) + '</div></div>';

      /* ---- WHAT CHANGED ------------------------------------------------ */
      var movers = mon.attention_movers || [];
      html += '<section id="today-changed"><h2>WHAT CHANGED</h2><div class="dec-band dec-band-2">' +
        '<div class="dec-cell wide"><h4>ATTENTION MOVEMENT (observed deltas only)</h4>' +
        (movers.length ? tableOrNone(movers, ["Artist", "As of", "Value", "Prior", "Change"], function (mv) {
          return row([linkTo("artists", mv.artist_key, mv.artist_name), fmt(mv.as_of), fmt(mv.value), fmt(mv.prior), deltaChip(mv.change, mv.value ? Math.round(10000 * mv.change / mv.value) / 100 : null)]);
        }) : compactEmpty("NO OBSERVED MOVEMENT", "Movement rows appear when a source reports two comparable observations with different values.")) + '</div>';
      var s = t.sections || {};
      var tk = s.ticketing || {};
      html += '<div class="dec-cell"><h4>TICKETING</h4>' +
        ((tk.new_onsales || []).length ? tableOrNone(tk.new_onsales.slice(0, 8), ["Event", "Onsale"], function (x) { return row([esc(x.event_name), fmt(x.onsale_start)]); })
          : compactEmpty("NO ONSALE CHANGES", "No new onsales since the last observation.")) + '</div></div></section>';

      /* ---- UPCOMING ---------------------------------------------------- */
      var upcoming = mon.upcoming || [];
      html += '<section id="today-upcoming"><h2>UPCOMING — WATCHED & SHORTLISTED</h2>' +
        (upcoming.length ? tableOrNone(upcoming, ["Date", "Artist", "Venue", "Market"], function (u) {
          return row([fmt(u.date), esc(u.artist_name), esc(u.venue || "UNKNOWN"), esc(u.market || "UNKNOWN")]);
        }) : compactEmpty("NO UPCOMING EVENTS", "No retained forward events for watched or shortlisted artists.")) + '</section>';

      /* ---- RECENT (local workflow memory) -------------------------------- */
      html += '<section id="today-recent"><h2>RECENT</h2>' +
        (recents.length ? tableOrNone(recents.slice(0, 10), ["When", "Entity", "Type"], function (r) {
          var kind = r.type === "artist" ? "artists" : r.type + "s";
          var label = r.type === "compare" ? "Compare " + r.id.split("|")[0].slice(0, 18) : r.name;
          return row([fmt(r.at), r.type === "compare" ? esc(label) : linkTo(kind, r.id, label), esc(r.type.toUpperCase())]);
        }) : compactEmpty("NO RECENT ENTITIES", "Entities you open are remembered here (local only).")) + '</section>';

      /* ---- data health (below the fold, compact) ------------------------- */
      if (s.data_health) {
        var providers = s.data_health.providers || [];
        var failing = providers.filter(function (p) { return p.operational_status !== "OPERATIONAL"; });
        html += '<section id="today-health"><h2>DATA HEALTH</h2>' +
          (providers.length ? '<div class="dec-row"><span class="dec-k">Providers</span><span class="dec-v">' + (providers.length - failing.length) + '/' + providers.length + ' operational</span></div>' +
            (failing.length ? '<div class="dec-row"><span class="dec-k">Not operational</span><span class="dec-reason">' + esc(failing.map(function (f) { return f.provider; }).join(" · ")) + '</span></div>' : "")
            : compactEmpty("NO PROVIDER STATE", "No acquisition provider health recorded.")) + '</section>';
      }
      content.innerHTML = html;
    });
  }

  function viewWatchlists() {
    setNav("watchlists");
    api("/api/watchlists").then(function (lists) {
      var html = "<h1>Watchlists</h1><p class='sub'>Named lists of artists, festivals, tours, events, venues, markets.</p>";
      html += "<div class='panel'><h3>Create watchlist</h3>" +
        "<input id='wl-name' placeholder='Name (e.g. 2027 Talent Targets)' /> " +
        "<select id='wl-etype'><option value='ARTIST'>Artist</option><option value='FESTIVAL'>Festival</option>" +
        "<option value='TOUR'>Tour</option><option value='EVENT'>Event</option><option value='VENUE'>Venue</option>" +
        "<option value='MARKET'>Market</option><option value='PROMOTER'>Promoter</option><option value='COMPANY'>Company</option></select> " +
        "<button id='wl-create'>Create</button></div>";
      html += "<div id='wl-list'>";
      if (!lists || !lists.length) { html += '<div class="none">No watchlists yet.</div>'; }
      lists.forEach(function (w) {
        html += "<h2>" + esc(w.name) + " <span class='muted'>" + esc(w.item_count) + " items" +
          (w.is_system ? " · system" : "") + "</span></h2>";
        html += "<p class='muted'>" + esc(w.description || "") + "</p>";
        html += "<div class='panel'><input id='add-" + esc(w.watchlist_key) + "-name' placeholder='Entity key (e.g. mbid::f4abc0b5…)' style='width:45%' /> " +
          "<input id='add-" + esc(w.watchlist_key) + "-label' placeholder='Display name' style='width:25%' /> " +
          "<select id='add-" + esc(w.watchlist_key) + "-etype'><option value='ARTIST'>Artist</option><option value='FESTIVAL'>Festival</option>" +
          "<option value='TOUR'>Tour</option><option value='EVENT'>Event</option><option value='VENUE'>Venue</option>" +
          "<option value='MARKET'>Market</option><option value='PROMOTER'>Promoter</option></select> " +
          "<button data-wl-add=\"" + esc(w.watchlist_key) + "\">Add</button></div>";
        html += "<div id='wl-" + esc(w.watchlist_key) + "'><div class='muted'>…</div></div>";
      });
      html += "</div>";
      content.innerHTML = html;

      var createBtn = document.getElementById("wl-create");
      if (createBtn) createBtn.addEventListener("click", function () {
        var name = document.getElementById("wl-name").value.trim();
        if (!name) return;
        api("/api/watchlists", { method: "POST", body: JSON.stringify({
          name: name, entity_type: document.getElementById("wl-etype").value, is_system: false }) })
          .then(function () { viewWatchlists(); });
      });
      document.querySelectorAll("[data-wl-add]").forEach(function (b) {
        b.addEventListener("click", function () {
          var key = b.getAttribute("data-wl-add");
          api("/api/watchlists/" + encodeURIComponent(key), { method: "POST", body: JSON.stringify({
            action: "add",
            entity_type: document.getElementById("add-" + key + "-etype").value,
            entity_key: document.getElementById("add-" + key + "-name").value.trim(),
            entity_name: document.getElementById("add-" + key + "-label").value.trim() || null }) })
            .then(function () { viewWatchlists(); });
        });
      });

      lists.forEach(function (w) {
        api("/api/watchlists/" + w.watchlist_key).then(function (items) {
          var el = document.getElementById("wl-" + w.watchlist_key);
          if (!el) return;
          var rows = (items || []).map(function (i) {
            var kind = String(i.entity_type || "").toLowerCase();
            return row(['<span class="pill ok">' + esc(i.entity_type) + "</span>",
              linkTo(kind + "s", i.entity_key, i.entity_name || i.entity_key),
              '<button data-wl-remove="' + esc(w.watchlist_key) + '" data-etype="' + esc(i.entity_type) + '" data-ekey="' + esc(i.entity_key) + '">Remove</button>']);
          });
          el.innerHTML = rows.length ? "<table><thead><tr><th>Type</th><th>Name</th><th></th></tr></thead><tbody>" + rows.join("") + "</tbody></table>" : '<div class="none">Empty list.</div>';
          el.querySelectorAll("[data-wl-remove]").forEach(function (btn) {
            btn.addEventListener("click", function () {
              api("/api/watchlists/" + encodeURIComponent(btn.getAttribute("data-wl-remove")), { method: "POST", body: JSON.stringify({
                action: "remove", entity_type: btn.getAttribute("data-etype"), entity_key: btn.getAttribute("data-ekey") }) })
                .then(function () { viewWatchlists(); });
            });
          });
        });
      });
    });
  }

  /* ---- BUILD: festival planning workspace ---------------------------- */

  function viewBuild() {
    setNav("build");
    api("/api/planning/projects").then(function (projects) {
      var html = "<h1>BUILD</h1><p class='sub'>Festival planning workspace — synthetic scenarios are clearly marked, never official.</p>";
      html += "<div class='panel'><h3>Create project</h3>" +
        "<input id='bp-name' placeholder='Festival name' /> " +
        "<input id='bp-city' placeholder='City / market' style='width:150px' /> " +
        "<input id='bp-start' type='date' /> <input id='bp-end' type='date' /> " +
        "<button id='bp-create'>Create</button> " +
        "<button id='bp-seed' class='nav-btn'>Seed synthetic Chicago 2027</button></div>";
      html += "<div id='bp-list'>";
      if (!projects || !projects.length) html += '<div class="none">No projects yet. Create one or seed the synthetic scenario.</div>';
      projects.forEach(function (p) {
        html += "<h2>" + linkTo("build", p.project_key, p.name) + " <span class='pill warn'>" + esc(p.scenario_class) + "</span></h2>" +
          "<p class='muted'>" + esc(p.city || "") + (p.start_date ? " · " + esc(p.start_date) + " → " + esc(p.end_date) : "") +
          " · " + esc(p.candidate_count || 0) + " candidates</p>";
      });
      html += "</div>";
      content.innerHTML = html;
      var create = document.getElementById("bp-create");
      if (create) create.addEventListener("click", function () {
        api("/api/planning/projects", { method: "POST", body: JSON.stringify({
          name: document.getElementById("bp-name").value.trim(),
          city: document.getElementById("bp-city").value.trim() || null,
          start_date: document.getElementById("bp-start").value || null,
          end_date: document.getElementById("bp-end").value || null,
          scenario_class: "SYNTHETIC_PLANNING_SCENARIO" }) })
          .then(function (p) { if (p.project_key) location.hash = "#/build/" + p.project_key; else viewBuild(); });
      });
      var seed = document.getElementById("bp-seed");
      if (seed) seed.addEventListener("click", function () {
        api("/api/planning/seed", { method: "POST" }).then(function (p) {
          if (p.project_key) location.hash = "#/build/" + p.project_key; else viewBuild();
        });
      });
    });
  }

  function viewBuildProject(id) {
    setNav("build");
    api("/api/planning/projects/" + encodeURIComponent(id)).then(function (p) {
      if (!p) { content.innerHTML = "<h1>Project</h1><div class='none'>Not found.</div>"; return; }
      var html = "<h1>" + esc(p.name) + "</h1>" +
        "<p class='sub'><span class='pill warn'>" + esc(p.scenario_class) + "</span> · " + esc(p.city || "") +
        (p.start_date ? " · " + esc(p.start_date) + " → " + esc(p.end_date) : "") +
        " · " + fmt(p.talent_budget_usd) + " talent budget (UNKNOWN if none entered)</p>";

      html += "<h2>Stages</h2>" + tableOrNone(p.stages, ["Stage", "Capacity claim", "Evidence", "Type"], function (s) {
        return row([esc(s.stage_name), fmt(s.capacity_claim), fmt(s.capacity_evidence_class), fmt(s.indoor_outdoor)]);
      });
      html += "<div class='panel'><input id='st-name' placeholder='Stage name' /> " +
        "<input id='st-cap' placeholder='Capacity (optional)' type='number' style='width:130px' /> " +
        "<button id='st-add' data-project='" + esc(p.project_key) + "'>Add stage</button></div>";

      html += "<h2>Candidates (" + esc(p.candidate_count) + ")</h2>" +
        "<div class='panel'><button id='cand-gen' data-project='" + esc(p.project_key) + "'>Generate candidate universe</button> " +
        "<input id='cand-add-name' placeholder='Artist name' /> " +
        "<input id='cand-add-key' placeholder='artist_key (optional)' style='width:220px' /> " +
        "<button id='cand-add' data-project='" + esc(p.project_key) + "'>Add</button></div>" +
        "<div id='cand-list'></div>";

      html += "<h2>Shortlist</h2><div id='sl-list'></div>";
      html += "<div class='panel'><button id='sl-refresh' data-project='" + esc(p.project_key) + "'>Refresh</button></div>";

      html += "<h2>Competitive calendar</h2><p class='sub'>What else is happening around the proposed date — raw counts and evidence, never a score. PIT label: NON_PIT means the current warehouse view, not historical knowability.</p>" +
        "<div class='panel'><label>Date <input id='cc-date' type='date' value='" + esc(p.start_date || "") + "' /></label> " +
        "<button id='cc-load' data-project='" + esc(p.project_key) + "'>Load calendar</button></div>" +
        "<div id='cc-view'><div class='none'>Choose a date and load the calendar.</div></div>";

      html += "<h2>Scenarios (non-optimizing)</h2>" +
        "<div class='panel'><input id='scen-name' placeholder='Scenario name' value='Day 1 v1' /> " +
        "<button id='scen-build' data-project='" + esc(p.project_key) + "'>Build board from shortlist</button></div>" +
        "<div id='scen-board'></div>" +
        "<div id='scen-list'></div>";
      html += "<h2>Show economics</h2><p class='sub'>Deterministic underwrites attached to this project. Scenario labels do not imply probability.</p>" +
        "<div id='econ-list'></div><div id='econ-compare'></div>";

      html += "<h2>Proposed shows</h2><p class='sub'>Unified ARTIST × MARKET × DATE × VENUE × DEAL underwriting objects. Evidence organized, never scored.</p>" +
        "<div class='panel'><input id='ps-artist' placeholder='Artist name' /> " +
        "<input id='ps-venue' placeholder='Venue name' /> " +
        "<input id='ps-date' type='date' /> " +
        "<input id='ps-guarantee' type='number' placeholder='Guarantee' style='width:100px' /> " +
        "<select id='ps-deal'><option value='FLAT_GUARANTEE'>Flat guarantee</option><option value='GUARANTEE_VS_PERCENTAGE'>Guarantee vs %</option><option value='PERCENTAGE'>Percentage</option></select> " +
        "<button id='ps-create' data-project='" + esc(p.project_key) + "'>Create proposed show</button></div>" +
        "<div id='ps-list'></div>" +
        "<button id='ps-compare-btn' data-project='" + esc(p.project_key) + "' style='margin-top:8px'>Compare proposed shows</button>";

      content.innerHTML = html;

      var stAdd = document.getElementById("st-add");
      if (stAdd) stAdd.addEventListener("click", function () {
        var name = document.getElementById("st-name").value.trim();
        if (!name) return;
        var cap = document.getElementById("st-cap").value;
        api("/api/planning/projects/" + encodeURIComponent(p.project_key) + "/stages", {
          method: "POST", body: JSON.stringify({ stage_name: name, capacity_claim: cap ? Number(cap) : null, capacity_evidence_class: cap ? "ESTIMATED" : null }) })
          .then(function () { viewBuildProject(p.project_key); });
      });
      var candGen = document.getElementById("cand-gen");
      if (candGen) candGen.addEventListener("click", function () {
        api("/api/planning/projects/" + encodeURIComponent(p.project_key) + "/candidates", {
          method: "POST", body: JSON.stringify({ generate: true, limit: 200 }) })
          .then(function () { viewBuildProject(p.project_key); });
      });
      var candAdd = document.getElementById("cand-add");
      if (candAdd) candAdd.addEventListener("click", function () {
        api("/api/planning/projects/" + encodeURIComponent(p.project_key) + "/candidates", {
          method: "POST", body: JSON.stringify({
            artist_name: document.getElementById("cand-add-name").value.trim(),
            artist_key: document.getElementById("cand-add-key").value.trim() || null }) })
          .then(function () { viewBuildProject(p.project_key); });
      });
      var slRefresh = document.getElementById("sl-refresh");
      if (slRefresh) slRefresh.addEventListener("click", function () { loadShortlist(p.project_key); });
      var scenBuild = document.getElementById("scen-build");
      if (scenBuild) scenBuild.addEventListener("click", function () { buildScenarioBoard(p.project_key); });
      var ccLoad = document.getElementById("cc-load");
      if (ccLoad) ccLoad.addEventListener("click", function () {
        loadCompetitiveCalendar(p.project_key, document.getElementById("cc-date").value || null);
      });
      if (p.start_date) loadCompetitiveCalendar(p.project_key, p.start_date);
      loadCandidates(p.project_key);
      loadShortlist(p.project_key);
      loadScenarios(p.project_key);
      loadEconomicsScenarios(p.project_key);
      loadProposedShows(p.project_key);

      var psCreate = document.getElementById("ps-create");
      if (psCreate) psCreate.addEventListener("click", function () {
        api("/api/planning/projects/" + encodeURIComponent(p.project_key) + "/proposed-shows", {
          method: "POST",
          body: JSON.stringify({
            artist_name: document.getElementById("ps-artist").value.trim(),
            venue_name: document.getElementById("ps-venue").value.trim(),
            proposed_date: document.getElementById("ps-date").value,
            artist_guarantee: Number(document.getElementById("ps-guarantee").value) || null,
            deal_type: document.getElementById("ps-deal").value,
            market: p.city || p.market || "",
            city: p.city,
            project_key: p.project_key,
          }),
        }).then(function (show) {
          if (show && show.proposed_show_key) location.hash = "#/build/" + encodeURIComponent(p.project_key) + "/proposed/" + encodeURIComponent(show.proposed_show_key);
          else loadProposedShows(p.project_key);
        });
      });

      var psCompareBtn = document.getElementById("ps-compare-btn");
      if (psCompareBtn) psCompareBtn.addEventListener("click", function () {
        location.hash = "#/build/" + encodeURIComponent(p.project_key) + "/compare-proposals";
      });
    });
  }

  function loadProposedShows(projectKey) {
    api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/proposed-shows").then(function (shows) {
      var el = document.getElementById("ps-list");
      if (!el) return;
      if (!shows || !shows.length) {
        el.innerHTML = '<div class="none">No proposed shows yet. Create one above.</div>';
        return;
      }
      el.innerHTML = "<table><thead><tr><th>Artist</th><th>Date</th><th>Venue</th><th>Deal</th><th>Guarantee</th><th>Rev</th></tr></thead><tbody>" +
        shows.map(function (s) {
          return "<tr><td>" + linkTo("build/" + encodeURIComponent(projectKey) + "/proposed", s.proposed_show_key, s.artist_name) + "</td>" +
            "<td>" + fmt(s.proposed_date) + "</td>" +
            "<td>" + fmt(s.venue_name) + "</td>" +
            "<td>" + fmt(s.deal_type) + "</td>" +
            "<td>" + (s.artist_guarantee ? "$" + s.artist_guarantee.toLocaleString() : "UNKNOWN") + "</td>" +
            "<td>" + s.current_revision + "</td></tr>";
        }).join("") + "</tbody></table>";
    });
  }

  function loadCompetitiveCalendar(projectKey, date) {
    var q = date ? "?date=" + encodeURIComponent(date) : "";
    api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/competitive-calendar" + q).then(function (cal) {
      var el = document.getElementById("cc-view");
      if (!el) return;
      if (!cal || cal.status !== "OBSERVED") {
        el.innerHTML = '<div class="none">No calendar evidence for this date/market yet.</div>';
        return;
      }
      var known = cal.known_at_cutoff || [];
      var post = cal.observed_after_cutoff || [];
      var unknown = cal.unknown_knowledge_time || [];
      var html = "<p class='muted'>" + esc(cal.target.date) + " · " + esc(cal.target.city || "") +
        " · <span class='pill warn'>" + esc(cal.pit_mode) + "</span></p>";
      html += "<table><thead><tr><th>Window</th><th>Known at cutoff</th><th>Observed after</th><th>Unknown</th></tr></thead><tbody>";
      ["pm0", "pm3", "pm7", "pm14"].forEach(function (w) {
        var cell = (cal.windows || {})[w] || {};
        var segCounts = function (bucket) {
          var m = cell[bucket] || {};
          return Object.keys(m).map(function (s) { return s + " " + m[s]; }).join(", ") || "0";
        };
        html += row([w === "pm0" ? "Same day" : "±" + w.slice(2) + " days",
          segCounts("known_before_cutoff"), segCounts("observed_post_cutoff"), segCounts("unknown_knowledge_time")]);
      });
      html += "</tbody></table>";
      var dist = cal.distance || {};
      html += "<p class='muted'>Distance: same venue " + fmt(dist.same_venue) +
        " · ≤5mi " + fmt(dist.within_5) + " · ≤10mi " + fmt(dist.within_10) +
        " · ≤25mi " + fmt(dist.within_25) + " · ≤50mi " + fmt(dist.within_50) + "</p>";
      var eventTable = function (rows) {
        if (!rows.length) return '<div class="none">None</div>';
        return "<table><thead><tr><th>Date</th><th>Event</th><th>Type</th><th>Venue</th><th>Distance</th><th>Window</th><th>Knowledge</th></tr></thead><tbody>" +
          rows.map(function (r) {
            return row([esc(r.event_date), esc(r.event_name),
              esc((r.segment || "") + (r.genre ? " / " + r.genre : "")),
              esc(r.venue_name || ""),
              r.distance_miles != null ? r.distance_miles + " mi" : "UNKNOWN",
              (r.windows || []).map(function (w) { return w === "pm0" ? "same day" : "±" + w.slice(2); }).join(", "),
              esc(r.knowledge_status || "")]);
          }).join("") + "</tbody></table>";
      };
      html += "<h3>Known at decision cutoff</h3>" + eventTable(known);
      html += "<h3>Observed after cutoff</h3><p class='muted'>Not knowable at the decision time — shown separately, never mixed into the known count.</p>" + eventTable(post);
      html += "<h3>Unknown knowledge time</h3>" + eventTable(unknown);
      el.innerHTML = html;
    });
  }

  function loadCandidates(projectKey) {
    api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/candidates").then(function (items) {
      var el = document.getElementById("cand-list");
      if (!el) return;
      if (!items || !items.length) { el.innerHTML = '<div class="none">No candidates yet — generate the universe or add artists.</div>'; return; }
      el.innerHTML = "<table><thead><tr><th>Artist</th><th>Reasons</th><th>Availability</th><th>Shortlist</th><th>Economics</th></tr></thead><tbody>" +
        items.map(function (c) {
          var reasons = (c.inclusion_reasons || []).map(function (r) { return '<span class="pill ok">' + esc(r.reason) + "</span>"; }).join(" ");
          var av = String(c.availability_status || "UNKNOWN");
          var cls = av === "NO_CONFLICT_OBSERVED" ? "warn" : av === "CONFIRMED_CONFLICT" ? "off" : "";
          var opts = ["DISCOVERED", "RESEARCHING", "INTEREST", "HOLD", "CONTACTED", "PASSED", "SHORTLIST", "UNKNOWN"]
            .map(function (st) { return '<option value="' + st + '">' + st + "</option>"; }).join("");
          return "<tr><td>" + linkTo("artists", c.artist_key || c.artist_name, c.artist_name) + "</td>" +
            "<td>" + (reasons || '<span class="muted">—</span>') + "</td>" +
            "<td><span class='pill " + cls + "'>" + esc(av) + "</span></td>" +
            "<td><select data-cand-sl='" + esc(projectKey) + "' data-key='" + esc(c.artist_key || "") + "' data-name='" + esc(c.artist_name) + "'>" + opts + "</select> " +
            "<button data-cand-sl-go='" + esc(projectKey) + "'>Add</button></td>" +
            "<td><button data-econ-open='" + esc(projectKey) + "' data-artist='" + esc(c.artist_key || c.artist_name) + "'>Underwrite</button></td></tr>";
        }).join("") + "</tbody></table>";
      el.querySelectorAll("[data-cand-sl-go]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var key = btn.getAttribute("data-cand-sl-go");
          var sel = btn.closest("tr").querySelector("[data-cand-sl]");
          api("/api/planning/projects/" + encodeURIComponent(key) + "/shortlist", {
            method: "POST", body: JSON.stringify({
              artist_key: sel.getAttribute("data-key") || null,
              artist_name: sel.getAttribute("data-name"), status: sel.value }) })
            .then(function () { loadShortlist(key); });
        });
      });
      el.querySelectorAll("[data-econ-open]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          location.hash = "#/build/" + encodeURIComponent(projectKey) + "/economics/" +
            encodeURIComponent(btn.getAttribute("data-artist"));
        });
      });
    });
  }

  function loadShortlist(projectKey) {
    api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/shortlist").then(function (items) {
      var el = document.getElementById("sl-list");
      if (!el) return;
      var statuses = ["DISCOVERED", "RESEARCHING", "INTEREST", "HOLD", "CONTACTED", "PASSED", "SHORTLIST", "UNKNOWN"];
      if (!items || !items.length) { el.innerHTML = '<div class="none">Shortlist empty — set a status from the candidate list or add directly.</div>'; return; }
      el.innerHTML = "<table><thead><tr><th>Artist</th><th>Status</th><th>Day</th><th>Stage</th><th>Billing</th><th>Economics</th></tr></thead><tbody>" +
        items.map(function (s) {
          var opts = statuses.map(function (st) {
            return '<option value="' + st + '"' + (s.status === st ? " selected" : "") + ">" + st + "</option>";
          }).join("");
          return "<tr><td>" + esc(s.artist_name) + "</td>" +
            "<td><select data-sl-status='" + esc(projectKey) + "' data-key='" + esc(s.artist_key || "") + "' data-name='" + esc(s.artist_name) + "'>" + opts + "</select></td>" +
            "<td>" + fmt(s.candidate_day) + "</td><td>" + fmt(s.candidate_stage) + "</td><td>" + fmt(s.candidate_billing_tier) + "</td>" +
            "<td><button data-econ-open='" + esc(projectKey) + "' data-artist='" + esc(s.artist_key || s.artist_name) + "'>Underwrite</button></td></tr>";
        }).join("") + "</tbody></table>";
      el.querySelectorAll("[data-sl-status]").forEach(function (sel) {
        sel.addEventListener("change", function () {
          api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/shortlist", {
            method: "POST", body: JSON.stringify({
              artist_key: sel.getAttribute("data-key") || null,
              artist_name: sel.getAttribute("data-name"), status: sel.value }) })
            .then(function () { loadShortlist(projectKey); });
        });
      });
      el.querySelectorAll("[data-econ-open]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          location.hash = "#/build/" + encodeURIComponent(projectKey) + "/economics/" +
            encodeURIComponent(btn.getAttribute("data-artist"));
        });
      });
    });
  }

  function loadScenarios(projectKey) {
    api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/scenarios").then(function (items) {
      var el = document.getElementById("scen-list");
      if (!el) return;
      if (!items || !items.length) { el.innerHTML = ""; return; }
      el.innerHTML = "<h3>Saved boards</h3><table><thead><tr><th>Name</th><th>Artists</th><th>Conflicts</th></tr></thead><tbody>" +
        items.map(function (sc) {
          var sum = sc.summaries || {};
          return row([esc(sc.name), fmt(sum.artist_count), fmt(sum.conflict_count)]);
        }).join("") + "</tbody></table>";
    });
  }

  function buildScenarioBoard(projectKey) {
    api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/shortlist").then(function (items) {
      var el = document.getElementById("scen-board");
      if (!el) return;
      if (!items || !items.length) { el.innerHTML = '<div class="none">Shortlist empty — add candidates and set statuses first.</div>'; return; }
      var html = "<h3>Scenario board</h3><p class='sub'>Hypothetical day × stage × slot placement. Warnings only — no optimization.</p>" +
        "<table><thead><tr><th>Artist</th><th>Day</th><th>Stage</th><th>Slot label</th><th>Billing tier</th></tr></thead><tbody>";
      items.forEach(function (s, i) {
        html += "<tr><td>" + esc(s.artist_name) + "</td>" +
          "<td><input type='number' min='1' value='1' id='sc-day-" + i + "' style='width:50px' /></td>" +
          "<td><input id='sc-stage-" + i + "' placeholder='Stage' value='" + esc(s.candidate_stage || "") + "' /></td>" +
          "<td><input id='sc-slot-" + i + "' placeholder='e.g. 18:00' /></td>" +
          "<td><input id='sc-tier-" + i + "' placeholder='HEADLINE' value='" + esc(s.candidate_billing_tier || "") + "' /></td></tr>";
      });
      html += "</tbody></table><button id='scen-save' data-project='" + esc(projectKey) + "'>Validate & save scenario</button>";
      html += "<div id='scen-warn'></div>";
      el.innerHTML = html;
      var save = document.getElementById("scen-save");
      if (save) save.addEventListener("click", function () {
        var slots = items.map(function (s, i) {
          return {
            artist_key: s.artist_key || null,
            artist_name: s.artist_name,
            day: Number(document.getElementById("sc-day-" + i).value) || null,
            stage: document.getElementById("sc-stage-" + i).value.trim() || null,
            slot_label: document.getElementById("sc-slot-" + i).value.trim() || null,
            billing_tier: document.getElementById("sc-tier-" + i).value.trim() || null,
          };
        });
        api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/scenarios", {
          method: "POST", body: JSON.stringify({ name: (document.getElementById("scen-name") || {}).value || "Scenario", slots: slots }) })
          .then(function (r) {
            var w = document.getElementById("scen-warn");
            var rows = (r.warnings || []).map(function (x) {
              return '<div class="warn-line"><span class="pill ' + (x.severity === "CONFIRMED" ? "off" : x.severity === "POSSIBLE" ? "warn" : "ok") + '">' + esc(x.severity) + "</span> " + esc(x.detail) + "</div>";
            });
            w.innerHTML = rows.length ? "<h4>Warnings</h4>" + rows.join("") : '<div class="none">No conflicts detected.</div>';
            loadScenarios(projectKey);
          });
      });
    });
  }

  /* ---- BUILD / show economics --------------------------------------- */

  function unknownInput() {
    return { value: null, provenance: "UNKNOWN", evidence_ref: null, as_of: null, entered_by: null };
  }

  function assumptionInput(value) {
    return { value: value, provenance: "USER_ASSUMPTION", evidence_ref: null, as_of: null, entered_by: null };
  }

  function defaultEconomicsInputs() {
    return {
      currency: assumptionInput("USD"),
      usable_capacity: unknownInput(),
      sellable_capacity: unknownInput(),
      ticket_scale: [{ name: "Scenario / GA", price: unknownInput(), quantity: unknownInput() }],
      sell_through: unknownInput(),
      ticketing_deduction_per_paid_ticket: unknownInput(),
      tax_rate_on_gross: unknownInput(),
      deal: {
        deal_type: assumptionInput("FLAT_GUARANTEE"), guarantee: unknownInput(),
        backend_percentage: unknownInput(), backend_basis: unknownInput(),
        artist_expenses: unknownInput(), approved_expense_names: unknownInput(),
      },
      costs: {
        marketing: unknownInput(), production: unknownInput(), venue: unknownInput(),
        labor: unknownInput(), insurance: unknownInput(), other: unknownInput(),
      },
      ancillary_revenue: unknownInput(), sponsorship_allocation: unknownInput(),
    };
  }

  function provenanceOptions(selected) {
    return ["USER_ASSUMPTION", "OBSERVED_PUBLIC", "OBSERVED_PRIVATE", "UNKNOWN"].map(function (value) {
      return '<option value="' + value + '"' + (selected === value ? " selected" : "") + ">" + value + "</option>";
    }).join("");
  }

  function econTypedField(id, label, item, type, choices) {
    item = item || unknownInput();
    var value = item.value == null ? "" : item.value;
    var input;
    if (choices) {
      input = '<select id="' + id + '"><option value="">UNKNOWN</option>' + choices.map(function (choice) {
        return '<option value="' + esc(choice) + '"' + (String(value) === choice ? " selected" : "") + ">" + esc(choice) + "</option>";
      }).join("") + "</select>";
    } else {
      input = '<input id="' + id + '" type="' + (type === "date" ? "date" : type === "text" || type === "tuple" ? "text" : "number") + '"' +
        (type === "decimal" ? ' step="any"' : "") + ' value="' + esc(value) + '" placeholder="UNKNOWN" />';
    }
    return '<div class="econ-field"><label for="' + id + '">' + esc(label) + "</label>" + input +
      '<select id="' + id + '-prov" class="prov prov-' + esc(item.provenance) + '">' + provenanceOptions(item.provenance) + "</select>" +
      '<details><summary>Evidence / audit</summary><input id="' + id + '-evidence" value="' + esc(item.evidence_ref || "") + '" placeholder="source or evidence reference" />' +
      '<input id="' + id + '-asof" value="' + esc(item.as_of || "") + '" placeholder="as-of time" />' +
      '<input id="' + id + '-entered" value="' + esc(item.entered_by || "") + '" placeholder="entered by" /></details></div>';
  }

  function readEconTyped(id, type) {
    var provenance = document.getElementById(id + "-prov").value;
    var raw = document.getElementById(id).value.trim();
    var evidence = document.getElementById(id + "-evidence").value.trim() || null;
    var asOf = document.getElementById(id + "-asof").value.trim() || null;
    var entered = document.getElementById(id + "-entered").value.trim() || null;
    if (provenance === "UNKNOWN" || raw === "") {
      return { value: null, provenance: "UNKNOWN", evidence_ref: evidence, as_of: asOf, entered_by: entered };
    }
    var value = type === "int" ? Number(raw) : type === "tuple" ? raw.split(",").map(function (x) { return x.trim(); }).filter(Boolean) : raw;
    return { value: value, provenance: provenance, evidence_ref: evidence, as_of: asOf, entered_by: entered };
  }

  function tierRowHtml(index, tier) {
    tier = tier || { name: "Tier " + (index + 1), price: unknownInput(), quantity: unknownInput() };
    return '<tr data-tier-row="' + index + '"><td><input id="econ-tier-name-' + index + '" value="' + esc(tier.name) + '" /></td>' +
      '<td>' + econTypedField("econ-tier-price-" + index, "Face price", tier.price, "decimal") + "</td>" +
      '<td>' + econTypedField("econ-tier-qty-" + index, "Sellable inventory", tier.quantity, "int") + "</td>" +
      '<td><button data-tier-remove="' + index + '">Remove</button></td></tr>';
  }

  function bindProvenanceControls(root) {
    root.querySelectorAll("select.prov").forEach(function (select) {
      function refresh() {
        select.className = "prov prov-" + select.value;
        var valueInput = document.getElementById(select.id.replace(/-prov$/, ""));
        if (select.value === "UNKNOWN") valueInput.value = "";
      }
      select.addEventListener("change", refresh);
      refresh();
    });
  }

  function readEconomicsInputs() {
    var tiers = Array.prototype.slice.call(document.querySelectorAll("tr[data-tier-row]")).map(function (tr) {
      var index = tr.getAttribute("data-tier-row");
      return {
        name: document.getElementById("econ-tier-name-" + index).value.trim() || "Unnamed tier",
        price: readEconTyped("econ-tier-price-" + index, "decimal"),
        quantity: readEconTyped("econ-tier-qty-" + index, "int"),
      };
    });
    return {
      currency: readEconTyped("econ-currency", "text"),
      usable_capacity: readEconTyped("econ-usable", "int"),
      sellable_capacity: readEconTyped("econ-sellable", "int"),
      ticket_scale: tiers,
      sell_through: readEconTyped("econ-sellthrough", "decimal"),
      ticketing_deduction_per_paid_ticket: readEconTyped("econ-ticket-deduction", "decimal"),
      tax_rate_on_gross: readEconTyped("econ-tax", "decimal"),
      deal: {
        deal_type: readEconTyped("econ-deal-type", "text"),
        guarantee: readEconTyped("econ-guarantee", "decimal"),
        backend_percentage: readEconTyped("econ-backend-pct", "decimal"),
        backend_basis: readEconTyped("econ-backend-basis", "text"),
        artist_expenses: readEconTyped("econ-artist-expenses", "decimal"),
        approved_expense_names: readEconTyped("econ-approved-expenses", "tuple"),
      },
      costs: {
        venue: readEconTyped("econ-cost-venue", "decimal"),
        production: readEconTyped("econ-cost-production", "decimal"),
        labor: readEconTyped("econ-cost-labor", "decimal"),
        marketing: readEconTyped("econ-cost-marketing", "decimal"),
        insurance: readEconTyped("econ-cost-insurance", "decimal"),
        other: readEconTyped("econ-cost-other", "decimal"),
      },
      ancillary_revenue: readEconTyped("econ-ancillary", "decimal"),
      sponsorship_allocation: readEconTyped("econ-sponsorship", "decimal"),
    };
  }

  function readEconomicsContext(project, artist) {
    function value(id) { return document.getElementById(id).value.trim() || null; }
    return {
      project_key: project.project_key, project_name: project.name,
      artist_key: artist.artist_key || null, artist_name: value("econ-artist"),
      venue: value("econ-venue"), market: value("econ-market"),
      event_date: value("econ-date"), event_configuration: value("econ-configuration"),
      holds: value("econ-holds"), kills: value("econ-kills"), comps: value("econ-comps"),
      offer_created_at: value("econ-offer-created"),
    };
  }

  function numericValue(id) {
    var node = document.getElementById(id);
    if (!node || node.value.trim() === "") return null;
    var value = Number(node.value);
    return Number.isFinite(value) ? value : null;
  }

  function whatIfPayload() {
    var axis = document.getElementById("econ-axis").value;
    var current = {
      sell_through: numericValue("econ-sellthrough"),
      average_ticket_price: numericValue("econ-tier-price-0"),
      artist_guarantee: numericValue("econ-guarantee"),
      sellable_capacity: numericValue("econ-sellable"),
      production_cost: numericValue("econ-cost-production"),
      marketing_cost: numericValue("econ-cost-marketing"),
    };
    var base = current[axis];
    var sensitivities = {};
    if (axis === "sell_through") sensitivities[axis] = ["0.60", "0.70", "0.80", "0.90", "1.00"];
    else if (base != null) sensitivities[axis] = [base * 0.8, base, base * 1.2].map(function (v) {
      return axis === "sellable_capacity" ? Math.floor(v) : v.toFixed(2);
    });
    var price = current.average_ticket_price;
    var capacity = current.sellable_capacity;
    var boundary = null;
    if (price != null && capacity != null) {
      boundary = {
        average_ticket_prices: [(price * 0.8).toFixed(2), price.toFixed(2), (price * 1.2).toFixed(2)],
        sellable_capacities: [capacity], sell_throughs: ["0.60", "0.70", "0.80", "0.90", "1.00"],
        minimum_contribution: document.getElementById("econ-hurdle").value || "0",
      };
    }
    return { sensitivities: sensitivities, boundary: boundary };
  }

  function outputDisplay(output) {
    if (!output || output.status !== "KNOWN") {
      var reason = output && output.reason ? " · " + esc(output.reason) : "";
      var missing = output && output.lineage ? " · inputs: " + esc(output.lineage.join(", ")) : "";
      return '<span class="unknown">' + esc(output ? output.status : "UNKNOWN") + "</span><span class='muted'>" + reason + missing + "</span>";
    }
    return '<span class="econ-value">' + esc(output.value) + (output.currency ? " " + esc(output.currency) : "") +
      '</span> <span class="pill prov-DERIVED">DERIVED</span>';
  }

  function renderEconomicsResult(result) {
    var el = document.getElementById("econ-results");
    if (!el) return;
    if (!result || result.error) {
      el.innerHTML = '<div class="error-line">' + esc((result && result.error) || "Calculation failed") + "</div>";
      return;
    }
    var outputs = result.evaluation.outputs;
    var core = [
      ["Gross Potential", "gross_potential"], ["Realized / Scenario Gross", "gross_ticket_revenue"],
      ["Artist Settlement", "artist_settlement"], ["Total Event Costs", "total_event_costs"],
      ["Promoter Contribution", "promoter_contribution"], ["Promoter Margin", "promoter_margin"],
    ];
    var reverse = [
      ["Break-even Tickets", "break_even_paid_tickets"], ["Break-even Sell-through", "break_even_sell_through"],
      ["Break-even ATP / Price", "break_even_average_ticket_price"], ["Break-even Sellable Capacity", "break_even_sellable_capacity"],
      ["Margin of Safety (tickets)", "margin_of_safety_tickets"], ["Artist Settlement Ceiling", "maximum_artist_settlement_at_break_even"],
      ["Flat Guarantee Ceiling", "maximum_flat_guarantee_at_break_even"], ["Additional Cost Headroom", "additional_cost_capacity"],
    ];
    function cards(items) {
      return '<div class="grid">' + items.map(function (item) {
        return '<div class="card"><h3>' + esc(item[0]) + "</h3>" + outputDisplay(outputs[item[1]]) + "</div>";
      }).join("") + "</div>";
    }
    var html = "<h2>Scenario economics</h2>" + cards(core) +
      '<h2>Break-even boundaries <span class="pill warn">SCENARIO BOUNDARY</span></h2>' + cards(reverse);
    var sensitivityRows = [];
    Object.keys(result.sensitivities || {}).forEach(function (axis) {
      result.sensitivities[axis].forEach(function (point) {
        sensitivityRows.push(row([esc(axis), esc(point.input), outputDisplay(point.promoter_contribution), outputDisplay(point.promoter_margin)]));
      });
    });
    html += "<h2>Sensitivity</h2><p class='sub'>WHAT-IF EQUATIONS — not probabilities or forecasts.</p>" +
      (sensitivityRows.length ? "<table><thead><tr><th>Axis</th><th>Input</th><th>Contribution</th><th>Margin</th></tr></thead><tbody>" + sensitivityRows.join("") + "</tbody></table>" : '<div class="none">Enter the selected axis to calculate a sensitivity table.</div>');
    if (result.boundaries && result.boundaries.length) {
      html += "<h2>Price × sell-through hurdle table</h2><table><thead><tr><th>ATP</th><th>Capacity</th><th>Sell-through</th><th>Paid tickets</th><th>Contribution</th><th>Meets hurdle</th></tr></thead><tbody>" +
        result.boundaries.map(function (point) {
          return row([esc(point.average_ticket_price), esc(point.sellable_capacity), esc(point.sell_through), esc(point.paid_tickets), outputDisplay(point.promoter_contribution), point.meets_hurdle == null ? "UNKNOWN" : point.meets_hurdle ? "YES" : "NO"]);
        }).join("") + "</tbody></table>";
    }
    el.innerHTML = html;
  }

  function loadEconomicsScenarios(projectKey) {
    api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/economics").then(function (items) {
      var el = document.getElementById("econ-list");
      if (!el) return;
      if (!items || !items.length) {
        el.innerHTML = '<div class="none">No economics scenarios yet. Open a candidate and choose Underwrite.</div>';
        return;
      }
      el.innerHTML = "<table><thead><tr><th>Compare</th><th>Scenario</th><th>Artist / venue</th><th>Currency</th><th>Revision</th><th>Saved</th></tr></thead><tbody>" +
        items.map(function (item) {
          var ctx = item.identity_context || {};
          var href = "#/build/" + encodeURIComponent(projectKey) + "/economics/" + encodeURIComponent(ctx.artist_key || ctx.artist_name || "booking-case") + "/" + encodeURIComponent(item.scenario_key);
          return row(['<input type="checkbox" data-econ-compare="' + esc(item.scenario_key) + '" />', '<a href="' + href + '">' + esc(item.name) + "</a>", esc((ctx.artist_name || "UNKNOWN") + " / " + (ctx.venue || "UNKNOWN")), fmt(item.currency), esc(item.revision_no), fmt(item.updated_at)]);
        }).join("") + "</tbody></table><button id='econ-compare-go'>Compare selected (2–4)</button>";
      document.getElementById("econ-compare-go").addEventListener("click", function () {
        var keys = Array.prototype.slice.call(document.querySelectorAll("[data-econ-compare]:checked")).map(function (node) { return node.getAttribute("data-econ-compare"); });
        var out = document.getElementById("econ-compare");
        if (keys.length < 2 || keys.length > 4) { out.innerHTML = '<div class="error-line">Select two to four scenarios.</div>'; return; }
        api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/economics/compare", { method: "POST", body: JSON.stringify({ scenario_keys: keys }) }).then(function (comparison) {
          if (comparison.error) { out.innerHTML = '<div class="error-line">' + esc(comparison.error) + "</div>"; return; }
          var useful = ["sellable_capacity", "weighted_average_ticket_price", "sell_through", "artist_guarantee", "total_event_costs", "gross_ticket_revenue", "artist_settlement", "promoter_contribution", "promoter_margin", "break_even_sell_through", "additional_cost_capacity"];
          var rows = (comparison.rows || []).filter(function (r) { return useful.indexOf(r.metric) >= 0; });
          out.innerHTML = "<h3>Scenario comparison</h3><p class='sub'>Differences from the first selected scenario. No ranking or winner.</p><table><thead><tr><th>Metric</th>" +
            comparison.scenarios.map(function (s) { return "<th>" + esc(s.name) + "</th>"; }).join("") + "</tr></thead><tbody>" +
            rows.map(function (r) { return "<tr><td>" + esc(r.metric) + "</td>" + r.values.map(function (v) { return "<td>" + (v.status === "KNOWN" ? esc(v.value) + (v.delta_from_baseline != null ? " Δ " + esc(v.delta_from_baseline) : "") : '<span class="unknown">' + esc(v.status) + "</span>") + "</td>"; }).join("") + "</tr>"; }).join("") + "</tbody></table>";
        });
      });
    });
  }

  function viewEconomics(projectKey, artistId, scenarioKey) {
    setNav("build");
    var requests = [
      api("/api/planning/projects/" + encodeURIComponent(projectKey)),
      api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/candidates"),
    ];
    if (scenarioKey) requests.push(api("/api/planning/economics/" + encodeURIComponent(scenarioKey)));
    Promise.all(requests).then(function (values) {
      var project = values[0];
      var candidates = values[1] || [];
      var saved = values[2] || null;
      var artist = candidates.filter(function (c) { return String(c.artist_key || c.artist_name) === artistId; })[0] || {
        artist_key: saved && saved.identity_context.artist_key || null,
        artist_name: saved && saved.identity_context.artist_name || artistId,
      };
      var context = saved ? saved.identity_context || {} : {};
      var inputs = saved ? saved.inputs : defaultEconomicsInputs();
      var html = '<p><a href="#/build/' + encodeURIComponent(projectKey) + '">← ' + esc(project.name) + "</a></p>" +
        "<h1>SHOW ECONOMICS</h1><p class='sub'>Interactive deterministic underwrite. This is not a forecast, recommendation, or probability model.</p>" +
        '<div class="panel econ-actions"><label>Scenario name <input id="econ-name" value="' + esc(saved ? saved.name : "BASE") + '" /></label> ' +
        '<button id="econ-calculate">Calculate</button> <button id="econ-save">Save</button> ' +
        (saved ? '<button id="econ-duplicate">Duplicate</button> <span class="pill">revision ' + esc(saved.revision_no) + " · " + esc(saved.engine_version) + "</span>" : "") +
        '<span id="econ-message"></span></div>' +
        "<h2>Identity / context</h2><p class='sub'>Context identifies the booking case; it does not imply economics.</p>" +
        '<div class="econ-grid"><div class="econ-field"><label>Artist</label><input id="econ-artist" value="' + esc(context.artist_name || artist.artist_name) + '" /></div>' +
        '<div class="econ-field"><label>Venue</label><input id="econ-venue" value="' + esc(context.venue || project.venue_site || "") + '" placeholder="UNKNOWN" /></div>' +
        '<div class="econ-field"><label>Market</label><input id="econ-market" value="' + esc(context.market || project.market || project.city || "") + '" placeholder="UNKNOWN" /></div>' +
        '<div class="econ-field"><label>Event date</label><input id="econ-date" type="date" value="' + esc(context.event_date || project.start_date || "") + '" /></div>' +
        '<div class="econ-field"><label>Event configuration</label><select id="econ-configuration"><option value="">UNKNOWN</option>' + ["CONCERT", "SEATED", "STANDING", "GA", "SPORTS"].map(function (v) { return '<option' + (context.event_configuration === v ? " selected" : "") + ">" + v + "</option>"; }).join("") + "</select></div>" +
        '<div class="econ-field"><label>Offer created at</label><input id="econ-offer-created" value="' + esc(context.offer_created_at || "") + '" placeholder="UNKNOWN" /></div></div>' +
        "<h2>Inventory</h2><div class='econ-grid'>" + econTypedField("econ-usable", "Usable capacity", inputs.usable_capacity, "int") + econTypedField("econ-sellable", "Sellable capacity", inputs.sellable_capacity, "int") +
        '<div class="econ-field unsupported"><label>Holds</label><input id="econ-holds" value="' + esc(context.holds || "") + '" placeholder="UNKNOWN" /><span class="pill off">SCHEMA READY · NOT CALCULATED</span></div>' +
        '<div class="econ-field unsupported"><label>Kills</label><input id="econ-kills" value="' + esc(context.kills || "") + '" placeholder="UNKNOWN" /><span class="pill off">SCHEMA READY · NOT CALCULATED</span></div>' +
        '<div class="econ-field unsupported"><label>Comps</label><input id="econ-comps" value="' + esc(context.comps || "") + '" placeholder="UNKNOWN" /><span class="pill off">SCHEMA READY · NOT CALCULATED</span></div></div>' +
        '<div class="panel"><button id="econ-prefill">Inspect compatible public capacity claims</button><div id="econ-prefill-result"></div></div>' +
        "<h2>Tickets</h2><p class='sub'>Tier prices and quantities are supported. Tier quantities must sum to sellable capacity. Paid tickets are derived from sellable capacity × assumed sell-through; explicit paid-ticket override is NOT_SUPPORTED_IN_V1.</p>" +
        '<table><thead><tr><th>Tier</th><th>Face price</th><th>Sellable inventory</th><th></th></tr></thead><tbody id="econ-tiers">' + inputs.ticket_scale.map(function (tier, i) { return tierRowHtml(i, tier); }).join("") + "</tbody></table><button id='econ-tier-add'>Add tier</button>" +
        '<div class="econ-grid">' + econTypedField("econ-sellthrough", "Assumed sell-through (0–1)", inputs.sell_through, "decimal") + econTypedField("econ-ticket-deduction", "Ticketing deduction / paid ticket", inputs.ticketing_deduction_per_paid_ticket, "decimal") + econTypedField("econ-tax", "Tax rate on gross (0–1)", inputs.tax_rate_on_gross, "decimal") + econTypedField("econ-currency", "Currency (ISO)", inputs.currency, "text") + "</div>" +
        "<h2>Artist deal</h2><div class='econ-grid'>" + econTypedField("econ-deal-type", "Deal type", inputs.deal.deal_type, "text", ["FLAT_GUARANTEE", "GUARANTEE_VS_PERCENTAGE", "PERCENTAGE_OF_DEFINED_BASE"]) + econTypedField("econ-guarantee", "Guarantee", inputs.deal.guarantee, "decimal") + econTypedField("econ-backend-pct", "Backend percentage (0–1)", inputs.deal.backend_percentage, "decimal") + econTypedField("econ-backend-basis", "Backend basis", inputs.deal.backend_basis, "text", ["GROSS_BOX_OFFICE", "ADJUSTED_GROSS", "NET_AFTER_APPROVED_EXPENSES"]) + econTypedField("econ-approved-expenses", "Approved expenses (comma-separated cost names)", inputs.deal.approved_expense_names, "tuple") + econTypedField("econ-artist-expenses", "Artist expenses", inputs.deal.artist_expenses, "decimal") + "</div><p class='sub'>Percentage deals require an explicit visible backend basis. Net-after-expenses also requires explicit approved cost names.</p>" +
        "<h2>Costs</h2><div class='econ-grid'>" + econTypedField("econ-cost-venue", "Venue", inputs.costs.venue, "decimal") + econTypedField("econ-cost-production", "Production", inputs.costs.production, "decimal") + econTypedField("econ-cost-labor", "Labor", inputs.costs.labor, "decimal") + econTypedField("econ-cost-marketing", "Marketing", inputs.costs.marketing, "decimal") + econTypedField("econ-cost-insurance", "Insurance", inputs.costs.insurance, "decimal") + econTypedField("econ-cost-other", "Other", inputs.costs.other, "decimal") + "</div>" +
        "<h2>Other revenues</h2><div class='econ-grid'>" + econTypedField("econ-ancillary", "Ancillary revenue", inputs.ancillary_revenue, "decimal") + econTypedField("econ-sponsorship", "Sponsorship allocation", inputs.sponsorship_allocation, "decimal") + "</div>" +
        "<h2>What-if controls</h2><div class='panel'><label>Axis <select id='econ-axis'><option value='sell_through'>Sell-through</option><option value='average_ticket_price'>Ticket price</option><option value='artist_guarantee'>Artist guarantee</option><option value='sellable_capacity'>Sellable capacity</option><option value='production_cost'>Production cost</option><option value='marketing_cost'>Marketing cost</option></select></label> <label>Contribution hurdle <input id='econ-hurdle' type='number' step='any' value='0' /></label></div>" +
        '<div id="econ-results"></div>';
      content.innerHTML = html;
      bindProvenanceControls(content);
      var tierCounter = inputs.ticket_scale.length;
      function bindTierRemoves() {
        document.querySelectorAll("[data-tier-remove]").forEach(function (button) {
          button.onclick = function () { if (document.querySelectorAll("tr[data-tier-row]").length > 1) button.closest("tr").remove(); };
        });
        bindProvenanceControls(document.getElementById("econ-tiers"));
      }
      bindTierRemoves();
      document.getElementById("econ-tier-add").onclick = function () {
        document.getElementById("econ-tiers").insertAdjacentHTML("beforeend", tierRowHtml(tierCounter, null));
        tierCounter += 1; bindTierRemoves();
      };
      function calculate() {
        var whatIf = whatIfPayload();
        return api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/economics/calculate", { method: "POST", body: JSON.stringify({ inputs: readEconomicsInputs(), sensitivities: whatIf.sensitivities, boundary: whatIf.boundary }) }).then(function (result) { renderEconomicsResult(result); return result; });
      }
      document.getElementById("econ-calculate").onclick = calculate;
      document.getElementById("econ-save").onclick = function () {
        api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/economics", { method: "POST", body: JSON.stringify({ name: document.getElementById("econ-name").value.trim(), scenario_key: saved ? saved.scenario_key : null, inputs: readEconomicsInputs(), identity_context: readEconomicsContext(project, artist) }) }).then(function (record) {
          if (record.error) { document.getElementById("econ-message").innerHTML = '<span class="error-line">' + esc(record.error) + "</span>"; return; }
          location.hash = "#/build/" + encodeURIComponent(projectKey) + "/economics/" + encodeURIComponent(artistId) + "/" + encodeURIComponent(record.scenario_key);
          viewEconomics(projectKey, artistId, record.scenario_key);
        });
      };
      if (saved) document.getElementById("econ-duplicate").onclick = function () {
        var name = document.getElementById("econ-name").value.trim() + " copy";
        api("/api/planning/economics/" + encodeURIComponent(saved.scenario_key) + "/duplicate", { method: "POST", body: JSON.stringify({ name: name }) }).then(function (record) {
          if (!record.error) location.hash = "#/build/" + encodeURIComponent(projectKey) + "/economics/" + encodeURIComponent(artistId) + "/" + encodeURIComponent(record.scenario_key);
        });
      };
      document.getElementById("econ-prefill").onclick = function () {
        var venue = document.getElementById("econ-venue").value.trim();
        var configuration = document.getElementById("econ-configuration").value;
        var target = document.getElementById("econ-prefill-result");
        if (!venue) { target.innerHTML = '<div class="error-line">Enter a venue identity first.</div>'; return; }
        api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/economics/prefill?venue=" + encodeURIComponent(venue) + "&configuration=" + encodeURIComponent(configuration)).then(function (prefill) {
          var claims = prefill.claims || [];
          target.innerHTML = '<p class="sub">' + esc(prefill.status) + ". Claims remain separate; maximum capacity is never copied into sellable capacity.</p>" +
            (prefill.usable_capacity_suggestion ? '<button id="econ-use-capacity">Use compatible claim as OBSERVED_PUBLIC usable capacity</button>' : "") +
            (claims.length ? "<table><thead><tr><th>Value</th><th>Type</th><th>Status</th><th>Source</th><th>As of</th></tr></thead><tbody>" + claims.map(function (claim) { return row([fmt(claim.capacity), fmt(claim.capacity_type), fmt(claim.claim_status), claim.source_url ? '<a target="_blank" rel="noopener" href="' + esc(claim.source_url) + '">' + esc(claim.source_provider) + "</a>" : fmt(claim.source_provider), fmt(claim.knowledge_time)]); }).join("") + "</tbody></table>" : '<div class="none">No stored public capacity claims. Enter a clearly marked user assumption if appropriate.</div>');
          if (prefill.usable_capacity_suggestion) document.getElementById("econ-use-capacity").onclick = function () {
            var suggestion = prefill.usable_capacity_suggestion;
            document.getElementById("econ-usable").value = suggestion.value;
            document.getElementById("econ-usable-prov").value = "OBSERVED_PUBLIC";
            document.getElementById("econ-usable-evidence").value = suggestion.evidence_ref || "";
            document.getElementById("econ-usable-asof").value = suggestion.as_of || "";
            document.getElementById("econ-usable-prov").dispatchEvent(new Event("change"));
          };
        });
      };
      if (saved) calculate();
    });
  }

  /* ---- COMPARE -------------------------------------------------------- */

  var compareSet = [];

  function viewCompare() {
    setNav("compare");
    var html = "<h1>COMPARE</h1><p class='sub'>Two-artist evidence review — no score, winner, fixed weights, or booking recommendation.</p>";
    html += "<div class='panel'><input id='cp-add' placeholder='Search artist to add…' style='width:40%' /> <button id='cp-add-btn'>Add artist</button></div>";
    html += "<div id='cp-set'></div><div id='cp-out'></div>";
    content.innerHTML = html;
    var btn = document.getElementById("cp-add-btn");
    if (btn) btn.addEventListener("click", function () {
      var q = document.getElementById("cp-add").value.trim();
      if (!q) return;
      api("/api/search?q=" + encodeURIComponent(q) + "&limit=5").then(function (hits) {
        if (!hits || !hits.length) return;
        var hit = hits[0];
        var key = hit.entity_id;
        if (compareSet.length >= 2) { compareSet.shift(); }
        if (compareSet.indexOf(key) === -1) compareSet.push(key);
        renderCompare();
      });
    });
    renderCompare();
  }

  function renderCompare() {
    var setEl = document.getElementById("cp-set");
    var outEl = document.getElementById("cp-out");
    if (!setEl || !outEl) return;
    if (!compareSet.length) {
      setEl.innerHTML = '<div class="none">Add exactly two artists to compare their evidence.</div>';
      outEl.innerHTML = "";
      return;
    }
    setEl.innerHTML = "<h3>Compare set (" + compareSet.length + ")</h3>" + compareSet.map(function (key) {
      return "<span class='pill ok'>" + esc(key) + " <button class='link' data-cp-remove='" + esc(key) + "'>✕</button></span> ";
    }).join("");
    setEl.querySelectorAll("[data-cp-remove]").forEach(function (b) {
      b.addEventListener("click", function () {
        compareSet = compareSet.filter(function (k) { return k !== b.getAttribute("data-cp-remove"); });
        renderCompare();
      });
    });
    if (compareSet.length < 2) {
      outEl.innerHTML = '<div class="none">Add one more artist. Comparison starts only when two governed Artist Security records are selected.</div>';
      return;
    }
    outEl.innerHTML = '<button id="cp-run">COMPARE EVIDENCE</button><p class="muted">The result shows source-separated facts and explicit UNKNOWN states; it never chooses a winner.</p>';
    document.getElementById("cp-run").onclick = function () {
      viewSecurityCompare(compareSet[0], compareSet[1]);
    };
  }

  function viewMonitors() {
    setNav("monitors");
    api("/api/monitors").then(function (items) {
      var html = "<h1>Saved monitors</h1><p class='sub'>Persisted column/filter/sort configurations.</p>";
      if (!items || !items.length) { content.innerHTML = html + '<div class="none">No saved monitors.</div>'; return; }
      html += "<table><thead><tr><th>Name</th><th>Entity</th><th>Columns</th><th>Horizon</th><th>Sort</th></tr></thead><tbody>";
      items.forEach(function (m) {
        var cols = [];
        try { cols = JSON.parse(m.visible_columns || "[]"); } catch (e) {}
        var sort = [];
        try { sort = JSON.parse(m.sort || "[]"); } catch (e) {}
        html += row([esc(m.name), esc(m.entity_type), esc(cols.join(", ")), esc(m.time_horizon || ""), esc(sort.map(function (s) { return s.field + " " + s.direction; }).join(", "))]);
      });
      content.innerHTML = html + "</tbody></table>";
    });
  }

  function viewAlerts() {
    setNav("alerts");
    api("/api/alerts?limit=200").then(function (items) {
      var html = "<h1>Alerts</h1><p class='sub'>Deterministic, source-backed changes. One logical change → one alert; re-runs never duplicate.</p>";
      if (!items || !items.length) { content.innerHTML = html + '<div class="none">No alerts.</div>'; return; }
      html += "<table><thead><tr><th>Observed</th><th>Type</th><th>Entity</th><th>Detail</th></tr></thead><tbody>";
      items.forEach(function (a) {
        var det = "";
        try { det = JSON.parse(a.detail || "{}"); } catch (e) {}
        html += row([fmt(a.observed_at ? String(a.observed_at).slice(0, 16) : null),
          '<span class="tape-type">' + esc(a.alert_type) + "</span>",
          esc(a.entity_name || a.entity_key),
          esc(det.event_name || det.age_days || "")]);
      });
      content.innerHTML = html + "</tbody></table>";
    });
  }

  function viewTours() {
    setNav("tours");
    api("/api/tours?limit=200").then(function (items) {
      var html = "<h1>Tours</h1><p class='sub'>Tour, residency, and run series from the reference graph.</p>";
      if (!items || !items.length) { content.innerHTML = html + '<div class="none">No tour series.</div>'; return; }
      html += "<table><thead><tr><th>Tour</th><th>Type</th><th>Date range</th><th>Events</th><th>Artists</th></tr></thead><tbody>";
      items.forEach(function (t) {
        html += row([linkTo("tours", t.series_key, t.name),
          '<span class="pill ok">' + esc(t.series_type) + "</span>",
          fmt(t.begin_date) + " → " + fmt(t.end_date),
          fmt(t.event_count), fmt(t.artist_count)]);
      });
      content.innerHTML = html + "</tbody></table>";
    });
  }

  function viewTour(id) {
    setNav("tours");
    api("/api/tours/" + encodeURIComponent(id)).then(function (t) {
      if (!t) { content.innerHTML = "<h1>Tour</h1><div class='none'>Not found.</div>"; return; }
      var html = "<h1>" + esc(t.name) + "</h1><p class='sub'>" + esc(t.series_type) + " · " +
        fmt(t.date_range[0]) + " → " + fmt(t.date_range[1]) + " · " + t.event_count + " events</p>";
      html += "<h2>Artists</h2>" + tableOrNone(t.performers, ["Artist", "Role"], function (p) {
        return row([linkTo("artists", p.artist_key || p.artist_mbid, p.artist_name), fmt(p.performer_role)]);
      });
      html += "<h2>Markets (" + t.markets.length + ")</h2>" + (t.markets.length
        ? "<ul>" + t.markets.map(function (m) { return "<li>" + esc(m) + "</li>"; }).join("") + "</ul>"
        : '<div class="none">No markets resolved.</div>');
      html += "<h2>Events</h2>" + tableOrNone(t.events, ["Date", "Event", "Venue", "Market"], function (e) {
        return row([fmt(e.local_date), esc(e.event_name), fmt(e.venue_name), fmt(e.market)]);
      });
      content.innerHTML = html;
    });
  }

  function viewData() {
    setNav("data");
    api("/api/sources").then(function (sources) {
      var html = "<h1>Data</h1><p class='sub'>Provider status and rights — restrictions are shown, not hidden.</p>";
      html += "<table><thead><tr><th>Provider</th><th>Access</th><th>Operational</th><th>Rights</th><th>Commercial</th></tr></thead><tbody>";
      (sources || []).forEach(function (s) {
        var op = (s.operational && s.operational.operational_status) || "NOT_MEASURED";
        var cls = op === "OPERATIONAL" ? "ok" : op === "NOT_CONFIGURED" ? "off" : "warn";
        html += row([
          esc(s.source_name || s.source_id),
          esc(s.access_status),
          '<span class="pill ' + cls + '">' + esc(op) + "</span>",
          esc(s.rights_status),
          esc(s.commercial_use_status),
        ]);
      });
      content.innerHTML = html + "</tbody></table>";
    });
  }

  function viewAsk() {
    setNav("ask");
    content.innerHTML =
      "<h1>ASK</h1><p class='sub'>Grounded read-only intelligence. Answers cite underlying evidence.</p>" +
      "<input id='ask-q' class='ask-input' placeholder='e.g. What changed in Chicago in the last seven days?' />" +
      "<button id='ask-go' class='nav-btn' style='margin-top:8px'>ASK</button>" +
      "<div id='ask-out' style='margin-top:12px'></div>";
    var go = function () {
      var q = document.getElementById("ask-q").value;
      var out = document.getElementById("ask-out");
      out.innerHTML = '<div class="muted">…</div>';
      fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      }).then(function (r) { return r.json(); }).then(function (a) {
        var html = "<div class='card'><h3>Answer</h3><p>" + esc(a.text) + "</p>" +
          "<p class='muted'>mode: " + esc(a.mode) + " · tool: " + esc(a.tool || "—") + "</p></div>";
        if (a.evidence && a.evidence.length) {
          html += "<pre class='evidence'>" + esc(JSON.stringify(a.evidence, null, 2)) + "</pre>";
        }
        out.innerHTML = html;
      });
    };
    document.getElementById("ask-go").onclick = go;
    document.getElementById("ask-q").onkeydown = function (e) { if (e.key === "Enter") go(); };
  }

  /* ---- BUILD: proposed shows ------------------------------------------ */

  function viewProposedShow(projectKey, showKey) {
    setNav("build");
    api("/api/planning/projects/" + encodeURIComponent(projectKey)).then(function (p) {
      api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/buyer-decision?show=" + encodeURIComponent(showKey)).then(function (view) {
        if (!view || view.status !== "OBSERVED") {
          content.innerHTML = '<p><a href="#/build/' + esc(projectKey) + '">← Build</a></p><h1>Proposed Show</h1><div class="none">Not found.</div>';
          return;
        }
        var h = view.header;
        var ev = view.evidence_status;
        var html = '<p><a href="#/build/' + esc(projectKey) + '">← ' + esc(p.name) + "</a></p>";

        // 1. SHOW HEADER
        html += "<h1>PROPOSED SHOW</h1>" +
          "<div class='card'><table><tr><th>Artist</th><td><strong>" + esc(h.artist_name) + "</strong></td>" +
          "<th>Market</th><td>" + esc(h.market) + "</td></tr>" +
          "<tr><th>Date</th><td>" + fmt(h.proposed_date) + "</td>" +
          "<th>Venue</th><td>" + esc(h.venue_name) + "</td></tr>" +
          "<tr><th>Configuration</th><td>" + fmt(h.venue_configuration) + "</td>" +
          "<th>Deal</th><td>" + fmt(h.deal_type) + "</td></tr>" +
          "<tr><th>Guarantee</th><td>" + (h.artist_guarantee ? "$" + h.artist_guarantee.toLocaleString() : "UNKNOWN") +
          " <span class='pill prov-" + esc(h.guarantee_provenance) + "'>" + esc(h.guarantee_provenance) + "</span></td>" +
          "<th>Decision cutoff</th><td>" + fmt(h.decision_cutoff) + "</td></tr>" +
          "<tr><th>Research cutoff</th><td>" + fmt(h.research_cutoff) + "</td>" +
          "<th>Revision</th><td>" + h.current_revision + "</td></tr></table></div>";

        // 2. EVIDENCE STATUS
        html += "<h2>Evidence status</h2><div style='display:flex;gap:8px'>" +
          "<span class='pill ok'>" + ev.KNOWN.length + " KNOWN</span>" +
          "<span class='pill warn'>" + ev.ASSUMED.length + " ASSUMED</span>" +
          "<span class='pill off'>" + ev.UNKNOWN.length + " UNKNOWN</span>" +
          "<span class='pill off'>" + ev.CONFLICTING.length + " CONFLICTING</span></div>";
        if (ev.ASSUMED.length) html += "<p class='muted'>Assumptions: " + ev.ASSUMED.map(function (f) { return '<code>' + esc(f) + '</code>'; }).join(", ") + "</p>";
        if (ev.UNKNOWN.length) html += "<p class='muted'>Unknown: " + ev.UNKNOWN.map(function (f) { return '<code>' + esc(f) + '</code>'; }).join(", ") + "</p>";

        // 3. VENUE / CAPACITY
        var vc = view.venue_capacity;
        html += "<h2>Venue capacity</h2>";
        html += "<p class='muted'>Status: <span class='pill " + (vc.status === "CONFIGURATION_COMPATIBLE" ? "ok" : "warn") + "'>" + esc(vc.status) + "</span></p>";
        var safePairs = (vc.assessment || {}).safe_pairs || [];
        var reviewPairs = (vc.assessment || {}).review_required_pairs || [];
        if (safePairs.length) html += "<p>Safe: " + safePairs.map(function (p) { return esc(p.configuration) + " → " + p.value; }).join(", ") + "</p>";
        if (reviewPairs.length) html += "<p class='warn-line'>⚠ Review required: " + reviewPairs.map(function (p) { return esc(p.configuration) + " (" + p.values.join("/") + ")"; }).join(", ") + "</p>";

        // 4. COMPETITIVE CALENDAR
        var cc = view.competitive_calendar;
        html += "<h2>Competitive calendar</h2>" +
          "<p class='muted'>PIT: <span class='pill warn'>" + esc(cc.pit_mode) + "</span>" +
          " · known: " + (cc.known_at_cutoff ? cc.known_at_cutoff.length : 0) +
          " · post: " + (cc.observed_after_cutoff ? cc.observed_after_cutoff.length : 0) +
          " · unknown time: " + (cc.unknown_knowledge_time ? cc.unknown_knowledge_time.length : 0) + "</p>";
        if (cc.distance) {
          html += "<p class='muted'>Distance: same venue " + fmt(cc.distance.same_venue) +
            " · ≤5mi " + fmt(cc.distance.within_5) + " · ≤10mi " + fmt(cc.distance.within_10) +
            " · ≤25mi " + fmt(cc.distance.within_25) + " · ≤50mi " + fmt(cc.distance.within_50) + "</p>";
        }

        // 5. COMPARABLE EVENTS
        var comp = view.comparable_events;
        html += "<h2>Comparable events</h2>";
        var gross = comp.gross || {};
        html += "<p>Gross: status " + esc(gross.status) + (gross.weighted_median != null ? " · median $" + gross.weighted_median : "") + "</p>";

        // 6. ARTIST CONTEXT
        var artist = view.artist_context;
        html += "<h2>Artist context</h2>" +
          "<p>Identity: " + (artist.identity && artist.identity.matched ? "resolved" : "UNKNOWN") + "</p>";

        // 7. SHOW ECONOMICS
        var econ = view.show_economics;
        html += "<h2>Show economics</h2><p class='muted'>Status: " + esc(econ.status) + "</p>";

        // 8. RISKS
        var risks = view.risks;
        html += "<h2>Risks & warnings</h2>";
        if (!risks.length) html += '<div class="none">No warnings detected.</div>';
        else risks.forEach(function (r) {
          var sev = r.severity === "WARNING" ? "warn" : r.severity === "ERROR" ? "off" : "ok";
          html += '<div><span class="pill ' + sev + '">' + esc(r.severity) + "</span> " + esc(r.type) + ": " + esc(r.detail) + "</div>";
        });

        // 9. PROVENANCE
        html += "<h2>Provenance</h2><p class='muted'>" + esc(view.provenance.competitive_calendar) + "</p>";
        html += "<p class='muted'>Sources: " + view.provenance.source_count + "</p>";

        // Actions
        html += "<div class='panel' style='margin-top:16px'>" +
          "<button id='ps-compare' data-project='" + esc(projectKey) + "' data-show='" + esc(showKey) + "'>Compare with another</button> " +
          "<button id='ps-revisions' data-project='" + esc(projectKey) + "' data-show='" + esc(showKey) + "'>Revision history</button></div>" +
          "<div id='ps-revisions-out'></div>";

        content.innerHTML = html;
        var compareBtn = document.getElementById("ps-compare");
        if (compareBtn) compareBtn.addEventListener("click", function () {
          location.hash = "#/build/" + encodeURIComponent(projectKey) + "/compare-proposals";
        });
        var revBtn = document.getElementById("ps-revisions");
        if (revBtn) revBtn.addEventListener("click", function () {
          api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/revisions?show=" + encodeURIComponent(showKey)).then(function (revs) {
            var out = document.getElementById("ps-revisions-out");
            if (!revs || !revs.length) { out.innerHTML = '<div class="none">No prior revisions.</div>'; return; }
            out.innerHTML = "<table><thead><tr><th>Revision</th><th>Date</th><th>Notes</th></tr></thead><tbody>" +
              revs.map(function (r) {
                return row([r.revision_number, fmt(r.created_at), fmt(r.notes)]);
              }).join("") + "</tbody></table>";
          });
        });
      });
    });
  }

  function viewCompareProposals(projectKey) {
    setNav("build");
    api("/api/planning/projects/" + encodeURIComponent(projectKey)).then(function (p) {
      api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/proposed-shows").then(function (shows) {
        if (!shows || !shows.length) {
          content.innerHTML = '<p><a href="#/build/' + esc(projectKey) + '">← Build</a></p><h1>Compare proposed shows</h1><div class="none">No proposed shows to compare. Create at least 2 shows first.</div>';
          return;
        }
        var html = '<p><a href="#/build/' + esc(projectKey) + '">← ' + esc(p.name) + "</a></p>" +
          "<h1>COMPARE PROPOSED SHOWS</h1><p class='sub'>Side-by-side evidence comparison. No ranking or recommendation.</p>" +
          "<p>Select two proposed shows to compare:</p>";
        shows.forEach(function (s) {
          html += '<div><input type="checkbox" class="ps-comp-cb" data-show="' + esc(s.proposed_show_key) + '" /> ' +
            esc(s.artist_name) + " · " + esc(s.proposed_date) + " · " + esc(s.venue_name || "") +
            " · $" + fmt(s.artist_guarantee) + " · " + esc(s.deal_type || "") + "</div>";
        });
        html += "<button id='ps-comp-go' data-project='" + esc(projectKey) + "'>Compare selected</button>";
        html += "<div id='ps-comp-result'></div>";
        content.innerHTML = html;

        document.getElementById("ps-comp-go").addEventListener("click", function () {
          var keys = Array.prototype.slice.call(document.querySelectorAll(".ps-comp-cb:checked")).map(function (cb) { return cb.getAttribute("data-show"); });
          var out = document.getElementById("ps-comp-result");
          if (keys.length !== 2) { out.innerHTML = '<div class="error-line">Select exactly two proposed shows.</div>'; return; }
          api("/api/planning/projects/" + encodeURIComponent(projectKey) + "/compare-proposals", {
            method: "POST",
            body: JSON.stringify({ proposed_show_keys: keys }),
          }).then(function (comparison) {
            if (!comparison || comparison.status !== "OBSERVED") {
              out.innerHTML = '<div class="error-line">Comparison failed.</div>'; return;
            }
            var table = comparison.comparison_table;
            out.innerHTML = "<h2>Side-by-side</h2><table><thead><tr><th>Dimension</th>" +
              comparison.scenarios.map(function (s) { return "<th>" + esc(s.header.artist_name) + " " + esc(s.header.venue_name || "") + " " + esc(s.header.proposed_date) + "</th>"; }).join("") +
              "</tr></thead><tbody>" +
              table.map(function (r) {
                return "<tr><th>" + esc(r.dimension) + "</th>" +
                  r.values.map(function (v, i) {
                    return "<td style='" + (r.differs ? "font-weight:bold;background:#fff3cd" : "") + "'>" + esc(v) + "</td>";
                  }).join("") + "</tr>";
              }).join("") + "</tbody></table>";

            // Show differences summary.
            if (comparison.differences.length) {
              out.innerHTML += "<p class='muted'>Differences: " + comparison.differences.map(function (d) { return esc(d.dimension); }).join(", ") + "</p>";
            }

            // Risk comparison
            out.innerHTML += "<h2>Risks per scenario</h2>";
            comparison.scenarios.forEach(function (s, i) {
              out.innerHTML += "<h3>Scenario " + (i + 1) + "</h3>" +
                (s.risks.length ? s.risks.map(function (r) { return '<div class="warn-line">' + esc(r.type) + ": " + esc(r.detail) + "</div>"; }).join("") : '<div class="none">No warnings.</div>');
            });
          });
        });
      });
    });
  }

  function tableOrNone(items, headers, render) {
    if (!items || !items.length) return '<div class="none">No data recorded. Unknown is not shown as zero.</div>';
    return "<table><thead><tr>" + headers.map(function (h) { return "<th>" + esc(h) + "</th>"; }).join("") +
      "</tr></thead><tbody>" + items.map(render).join("") + "</tbody></table>";
  }

  /* ---- routing --------------------------------------------------------- */

  var VIEWS = { today: viewToday, watchlists: viewWatchlists, monitors: viewMonitors, alerts: viewAlerts, tours: viewTours, tape: viewTape, status: viewStatus, news: viewNews, attention: viewAttention, artists: null, events: null, venues: null, markets: null, festivals: viewFestivals, build: viewBuild, compare: viewCompare, data: viewData, ask: viewAsk };

  function route() {
    var hash = location.hash.replace(/^#\/?/, "");
    var parts = hash.split("/").filter(Boolean);
    var view = parts[0] || "tape";
    var id = parts[1];
    if (view === "artists" && id) return viewArtistSecurity(decodeURIComponent(id));
    if (view === "compare-security" && id && parts[2]) return viewSecurityCompare(decodeURIComponent(id), decodeURIComponent(parts[2]));
    if (view === "events" && id) return viewEvent(decodeURIComponent(id));
    if (view === "venues" && id) return viewVenue(decodeURIComponent(id));
    if (view === "markets" && id) return viewMarket(decodeURIComponent(id));
    if (view === "festivals" && id) return viewFestival(decodeURIComponent(id));
    if (view === "tours" && id) return viewTour(decodeURIComponent(id));
    if (view === "build" && id && parts[2] === "economics" && parts[3]) {
      return viewEconomics(decodeURIComponent(id), decodeURIComponent(parts[3]), parts[4] ? decodeURIComponent(parts[4]) : null);
    }
    if (view === "build" && id && parts[2] === "proposed" && parts[3]) {
      return viewProposedShow(decodeURIComponent(id), decodeURIComponent(parts[3]));
    }
    if (view === "build" && id && parts[2] === "compare-proposals") {
      return viewCompareProposals(decodeURIComponent(id));
    }
    if (view === "build" && id) return viewBuildProject(decodeURIComponent(id));
    if (VIEWS[view]) return VIEWS[view]();
    return viewTape();
  }

  /* ---- events ---------------------------------------------------------- */

  document.addEventListener("click", function (ev) {
    var nav = ev.target.closest("[data-view]");
    if (nav) {
      location.hash = "#/" + nav.getAttribute("data-view");
      searchResults.classList.add("hidden");
      return;
    }
    var el = ev.target.closest("[data-nav]");
    if (el) {
      var type = el.getAttribute("data-nav");
      var id = el.getAttribute("data-id");
      var ctx = el.getAttribute("data-ctx");
      if (ctx) {
        try { sessionStorage.setItem("fi.context", ctx); } catch (e) { /* degrade */ }
      }
      location.hash = id ? "#/" + type + "/" + encodeURIComponent(id) : "#/" + type;
      searchResults.classList.add("hidden");
      searchInput.value = "";
    }
  });

  searchInput.addEventListener("input", function () {
    srSelected = -1;
    var q = searchInput.value.trim();
    if (q.length < 2) { searchResults.classList.add("hidden"); return; }
    api("/api/search?q=" + encodeURIComponent(q) + "&limit=10").then(function (items) {
      if (!items || !items.length) { searchResults.classList.add("hidden"); return; }
      searchResults.innerHTML = items.map(function (r) {
        return '<div class="sr-item" data-nav="' + r.entity_type.toLowerCase() + 's" data-id="' + esc(r.entity_id) + '">' +
          '<span class="sr-type">' + esc(r.entity_type) + "</span>" + esc(r.name) + "</div>";
      }).join("");
      searchResults.classList.remove("hidden");
    });
  });
  var srSelected = -1;
  function srSelect(idx) {
    var items = searchResults.querySelectorAll(".sr-item");
    if (!items.length) return;
    srSelected = Math.max(0, Math.min(idx, items.length - 1));
    items.forEach(function (el, i) { el.classList.toggle("selected", i === srSelected); });
    items[srSelected].scrollIntoView({ block: "nearest" });
  }
  searchInput.addEventListener("keydown", function (e) {
    var items = searchResults.querySelectorAll(".sr-item");
    if (e.key === "ArrowDown" && items.length) { e.preventDefault(); srSelect(srSelected < 0 ? 0 : srSelected + 1); return; }
    if (e.key === "ArrowUp" && items.length) { e.preventDefault(); srSelect(srSelected < 0 ? items.length - 1 : srSelected - 1); return; }
    if (e.key === "Enter") {
      searchResults.classList.add("hidden");
      if (srSelected >= 0 && items[srSelected]) {
        var el = items[srSelected];
        location.hash = "#/" + el.getAttribute("data-nav") + "/" + encodeURIComponent(el.getAttribute("data-id"));
        searchInput.value = "";
        srSelected = -1;
        return;
      }
      var q = searchInput.value.trim();
      if (!q) return;
      api("/api/search?q=" + encodeURIComponent(q) + "&limit=10").then(function (hits) {
        var artist = (hits || []).filter(function (item) { return item.entity_type === "ARTIST"; })[0];
        if (artist) location.hash = "#/artists/" + encodeURIComponent(artist.entity_id);
        else viewSearch(q);
      });
    }
  });
  searchResults.addEventListener("click", function (ev) {
    var el = ev.target.closest("[data-nav]");
    if (el) {
      location.hash = "#/" + el.getAttribute("data-nav") + "/" + encodeURIComponent(el.getAttribute("data-id"));
      searchResults.classList.add("hidden");
      searchInput.value = "";
      srSelected = -1;
    }
  });

  document.addEventListener("keydown", function (e) {
    var tag = (document.activeElement && document.activeElement.tagName) || "";
    var typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    if ((e.key === "/" || (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey))) && !typing) {
      e.preventDefault();
      searchInput.focus();
      searchInput.select();
    } else if (e.key === "Escape" && !searchResults.classList.contains("hidden")) {
      searchInput.value = "";
      searchResults.classList.add("hidden");
    }
  });

  window.addEventListener("hashchange", route);
  route();
})();
