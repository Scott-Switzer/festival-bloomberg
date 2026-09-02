# Festival Bloomberg — Talent Buyer Terminal

A research tool that answers one question about live music:

> **Before I book an artist, what do we actually know — and what do we not know?**

Festival Bloomberg is not a booking system. It does not predict sales, invent
guarantees, or tell you to BOOK or PASS. It gathers real, traceable evidence
about 25,000 artists — who they are, where they have played, who their
audience overlaps with, which festivals they have appeared at, and what shows
are currently listed — and lays that evidence out so a human can decide.

---

## The problem it solves

Booking a festival or venue slot means judging an artist on very little
information. Most of the industry runs on gut feel, hype, and hearsay.

This terminal replaces that with a *browser over evidence*:

- **Identity** — who is this artist? (MusicBrainz reference identity)
- **Market footprint** — where have they actually played, and how often?
- **Live history** — concrete past shows with dates, venues, cities.
- **Festival history** — festival and series appearances, with co-billed artists.
- **Audience** — shared-listener relationships from a 1% ListenBrainz sample
  (who else do their listeners already follow?).
- **Attention** — descriptive listening/channel observations, source by source.
- **Forward evidence** — currently listed future shows with advertised prices.

Every fact carries a **source** and a **knowledge time**. When we have no
evidence for something, the product says **UNKNOWN** — it never quietly
pretends the answer is zero.

---

## 3-minute walkthrough

1. **Launch** — run `./scripts/run_terminal.sh` and open the printed URL.
   (First launch downloads the ~120 MB evidence database from the cloud and
   verifies it by checksum — this is the only setup.)
2. **Home** — the screen shows *what is happening now* (recently listed
   shows), *what you are evaluating* (your shortlist), and a set of
   *start-here artists* with the deepest evidence.
3. **Open an artist** — click any start-here card (or search). You see a
   quick summary in the first seconds: tier, how many evidence panels are
   observed, live footprint, audience availability, forward shows.
4. **Inspect a market** — click a market on the artist's footprint (e.g.
   Chicago) to see every artist with observed shows there, ranked by density.
5. **Alternatives** — each artist page lists evidence-supported alternatives
   with *why* they are related (shared listeners, shared markets, shared
   festival bills).
6. **Compare** — pick two artists and the terminal lays them side by side:
   audience overlap, markets, live history, festivals, forward events,
   attention. No winner is declared.
7. **Shortlist** — add candidates, attach a market/date/venue/notes, compare
   any two from the shortlist, and reload — everything persists.

The whole loop takes about 3 minutes and never requires touching a database
or reading code.

---

## Five best demo artists

| Artist | Why |
|---|---|
| **Alice Cooper** | 1,450 live events, 57 markets, strong audience peers, 4 forward shows |
| **Barry Manilow** | 3,674 live events, 53 markets, 27 forward shows — one of the deepest files |
| **Ed Sheeran** | 283 events, 48 markets, festival + forward evidence |
| **Metallica** | 6/7 evidence families observed, great for Alternatives + Compare |
| **Olivia Rodrigo** | 6/7 families, strong audience-peer signal, forward evidence |

All five have audience peers, market evidence, live history, and attention —
so no page will be a wall of UNKNOWN.

---

## What the evidence means (and doesn't mean)

- **UNKNOWN ≠ 0.** A missing panel is a statement about our evidence, not a
  zero for the artist.
- **Audience sample ≠ total fans.** Audience relationships come from a 1%
  ListenBrainz pilot sample. "7 shared listeners" means 7 in the sample —
  it is a directional signal, not a census.
- **Listing ≠ sale.** Forward events are provider-listed shows with
  *advertised* price ranges. That is not attendance, sell-through, or revenue.
- **Place ≠ venue.** A market (Chicago) is not a specific venue.
- **No fake numbers.** No guarantees, gross, ROI, or BOOK/PASS is ever shown.

---

## Current limitations

- Audience/attention evidence covers the 1% ListenBrainz pilot sample only;
  Wikimedia and YouTube attention panels are not yet materialized in this
  generation (shown honestly as UNKNOWN).
- Advertised ticket ranges exist only where Ticketmaster Discovery listed
  them; "0 ranges" does not mean the show is sold out or unlisted elsewhere.
- Some identity fields (artist type, origin area) are empty for many artists.
- The terminal runs locally by default; a hosted read-only demo is on the
  roadmap.

---

## What comes next

1. Wikimedia + YouTube attention panels in the serving build.
2. Automated nightly refresh of the evidence database from new R2 assets.
3. A hosted read-only demo you can open from any device.
4. Bigger audience samples (beyond the 1% pilot) as licensing allows.

---

*Festival Bloomberg — evidence first, judgment yours.*
