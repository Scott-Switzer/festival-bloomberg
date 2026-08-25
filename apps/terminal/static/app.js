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

  function linkTo(type, id, label) {
    return '<button class="link" data-nav="' + type + '" data-id="' + esc(id) + '">' + esc(label) + "</button>";
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
    api("/api/markets/" + encodeURIComponent(id)).then(function (m) {
      if (!m) { content.innerHTML = "<h1>Market</h1><div class='none'>Not found.</div>"; return; }
      var html = "<h1>" + esc(m.name) + "</h1><p class='sub'>MARKET · " +
        m.upcoming_count + " upcoming · " + m.history_count + " historical</p>";
      html += "<h2>Upcoming events</h2>" + tableOrNone(m.upcoming, ["Date", "Artist", "Venue", "Status"], function (e) {
        return row([fmt(e.event_date), esc(e.artist_name), esc(e.venue_name), esc(e.event_status)]);
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
    api("/api/today").then(function (t) {
      var html = "<h1>Today</h1><p class='sub'>What changed in your music universe since you last checked.</p>";
      if (!t || !t.sections) { content.innerHTML = html + '<div class="none">No data.</div>'; return; }
      var s = t.sections;
      if (s.watchlist) {
        html += "<h2>Watchlist (" + esc(s.watchlist.watched_entities) + " watched)</h2>" +
          tableOrNone(s.watchlist.new_events, ["Name", "Type", "First seen"], function (x) {
            var kind = String(x.entity_type || "").toLowerCase();
            return row([linkTo(kind + "s", x.entity_key, x.entity_name || x.entity_key), esc(x.entity_type), fmt(x.first_seen_at)]);
          });
      }
      if (s.ticketing) {
        html += "<h2>Ticketing</h2>" + tableOrNone(s.ticketing.new_onsales, ["Event", "Onsale", "Source"], function (x) {
          return row([esc(x.event_name), fmt(x.onsale_start), esc(x.provider || "")]);
        });
        html += tableOrNone(s.ticketing.new_presales, ["Event", "Presale", "Source"], function (x) {
          return row([esc(x.event_name), fmt(x.presale_start), esc(x.provider || "")]);
        });
        html += tableOrNone(s.ticketing.status_changes, ["Event", "Status", "Observed"], function (x) {
          return row([esc(x.event_name), esc(x.status), fmt(x.observed_at)]);
        });
      }
      if (s.attention) {
        html += "<h2>Attention</h2>" + tableOrNone(s.attention.movers, ["Artist", "Metric", "Value", "Provider", "Period"], function (x) {
          var val = (x.value_sum != null ? x.value_sum : x.value);
          var unit = x.value_unit ? " " + x.value_unit : "";
          return row([linkTo("artists", x.artist_key, x.artist_name || x.artist_key), esc(x.metric_kind), fmt(val) + unit, esc(x.source_system), fmt(x.period_start || x.retrieved_at)]);
        });
      }
      if (s.live_market) {
        html += "<h2>Live market</h2>" + tableOrNone(s.live_market.busy_markets, ["Market", "Events"], function (x) {
          return row([esc(x.market), fmt(x.event_count)]);
        });
      }
      if (s.data_health) {
        html += "<h2>Data health</h2>" + tableOrNone(s.data_health.providers, ["Provider", "Status", "Failures", "Rate-limited", "Last success"], function (x) {
          var cls = x.operational_status === "OPERATIONAL" ? "ok" : x.operational_status === "NOT_CONFIGURED" ? "off" : "warn";
          return row([esc(x.provider), '<span class="pill ' + cls + '">' + esc(x.operational_status || "") + "</span>", fmt(x.failure_count), fmt(x.rate_limit_count), fmt(x.last_seen)]);
        });
        html += tableOrNone(s.data_health.identity_conflicts, ["Artist", "Issue"], function (x) {
          return row([esc(x.artist_key), esc(x.issue || "")]);
        });
      }
      content.innerHTML = html || ("<div class='none'>Nothing to show yet.</div>");
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
    var html = "<h1>COMPARE</h1><p class='sub'>Side-by-side artist investigation — evidence, not a recommendation. No winner badge.</p>";
    html += "<div class='panel'><input id='cp-add' placeholder='Search artist to add…' style='width:40%' /> <button id='cp-add-btn'>Add</button></div>";
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
        if (compareSet.length >= 10) { compareSet.shift(); }
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
      setEl.innerHTML = '<div class="none">Add 2–10 artists to compare.</div>';
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
    outEl.innerHTML = '<div class="muted">Loading scorecards…</div>';
    Promise.all(compareSet.map(function (key) {
      return api("/api/planning/scorecard?artist_key=" + encodeURIComponent(key)).then(function (sc) {
        if (sc && !sc.artist_name) return api("/api/planning/scorecard?artist_name=" + encodeURIComponent(key));
        return sc;
      });
    })).then(function (cards) {
      renderCompareTable(cards);
    });
  }

  function compCell(card, pick) {
    var v = pick(card);
    if (v == null || v === "" || (typeof v === "number" && isNaN(v))) return '<span class="muted">—</span>';
    return esc(typeof v === "number" ? String(Math.round(v * 100) / 100) : String(v));
  }

  function renderCompareTable(cards) {
    var outEl = document.getElementById("cp-out");
    if (!outEl) return;
    var rows = [
      ["Artist", function (c) { return c.artist_name; }],
      ["Identity", function (c) { var i = c.identity || {}; return i.type ? i.type + (i.area ? " · " + i.area : "") : "UNKNOWN"; }],
      ["External IDs", function (c) { var ids = Object.keys((c.identity || {}).external_ids || {}); return ids.length ? ids.join(", ") : "—"; }],
      ["Upcoming shows", function (c) { var l = c.live || {}; return l.upcoming_count; }],
      ["Historical shows", function (c) { var l = c.live || {}; return l.historical_count; }],
      ["Festival editions", function (c) { var f = c.festival || {}; return f.edition_count; }],
      ["Festival billing tiers", function (c) { var f = c.festival || {}; var t = Object.keys(f.billing_tiers || {}); return t.length ? t.join(", ") : "—"; }],
      ["Attention", function (c) { var a = c.attention || {}; return a.wikimedia ? "wikimedia " + a.wikimedia.value_sum : a.latest && a.latest.length ? a.latest[0].source_system : "—"; }],
      ["Gross comp median", function (c) { var g = (c.comparables || {}).gross || {}; return g.status === "OBSERVED" || g.status === "DERIVED" ? g.weighted_median : "UNKNOWN"; }],
      ["Gross comp range", function (c) { var g = (c.comparables || {}).gross || {}; return (g.status === "OBSERVED" || g.status === "DERIVED") && g.p10 != null ? g.p10 + "–" + g.p90 : "—"; }],
      ["Attendance comp median", function (c) { var a = (c.comparables || {}).attendance || {}; return a.status === "OBSERVED" || a.status === "DERIVED" ? a.weighted_median : "UNKNOWN"; }],
      ["Market shows", function (c) { var m = c.market_history || {}; return m.shows_in_market; }],
      ["Data coverage", function (c) { return (c.coverage || {}).coverage_score; }],
      ["Sources known", function (c) { return (c.coverage || {}).known_source_count; }],
    ];
    var html = "<table><thead><tr><th>Attribute</th>" + cards.map(function (c) {
      return "<th>" + esc(c.artist_name || c.artist_key || "?") + "</th>";
    }).join("") + "</tr></thead><tbody>";
    rows.forEach(function (r) {
      html += "<tr><th>" + esc(r[0]) + "</th>" + cards.map(function (c) { return "<td>" + compCell(c, r[1]) + "</td>"; }).join("") + "</tr>";
    });
    html += "</tbody></table>";
    html += "<p class='muted'>Evidence is per-scorecard; UNKNOWN cells are shown explicitly, never as zero. Comparable medians are RESEARCH-ONLY ranges." +
      " <a href='#/artists/" + esc(cards[0].artist_key || "") + "'>Open full scorecard</a></p>";
    outEl.innerHTML = html;
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
    if (view === "artists" && id) return viewArtist(decodeURIComponent(id));
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
      location.hash = id ? "#/" + type + "/" + encodeURIComponent(id) : "#/" + type;
      searchResults.classList.add("hidden");
    }
  });

  searchInput.addEventListener("input", function () {
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
  searchInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      searchResults.classList.add("hidden");
      viewSearch(searchInput.value);
    }
  });
  searchResults.addEventListener("click", function (ev) {
    var el = ev.target.closest("[data-nav]");
    if (el) {
      location.hash = "#/" + el.getAttribute("data-nav") + "/" + encodeURIComponent(el.getAttribute("data-id"));
      searchResults.classList.add("hidden");
    }
  });

  window.addEventListener("hashchange", route);
  route();
})();
