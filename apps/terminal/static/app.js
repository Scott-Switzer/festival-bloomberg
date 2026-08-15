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

  async function api(path) {
    var res = await fetch(path);
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

  function tableOrNone(items, headers, render) {
    if (!items || !items.length) return '<div class="none">No data recorded. Unknown is not shown as zero.</div>';
    return "<table><thead><tr>" + headers.map(function (h) { return "<th>" + esc(h) + "</th>"; }).join("") +
      "</tr></thead><tbody>" + items.map(render).join("") + "</tbody></table>";
  }

  /* ---- routing --------------------------------------------------------- */

  var VIEWS = { tape: viewTape, status: viewStatus, artists: null, events: null, venues: null, markets: null, festivals: viewFestivals, data: viewData, ask: viewAsk };

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
