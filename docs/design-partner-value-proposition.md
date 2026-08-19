# Design Partner Value Proposition

A one-page brief you can send to an independent promoter, venue group, or
regional festival operator.

---

## What Festival Bloomberg is

A local, provenance-first intelligence workstation for the **live music
business**. It combines an artist identity graph, live-event history, festival
and tour structure, attention signals, and historical event economics into a
talent-buyer workflow (discover → compare → shortlist → scenario).

Its core promise: **every number has a source, a point-in-time, and a rights
status.** Unknown is shown as unknown — never faked.

## What data we want

Your **sanitized historical event history** — no customer PII. The fields that
matter most, in order of value:

1. **decision dates** — offer, booking, announcement, onsale
2. **capacity** — venue, usable, ticket capacity
3. **tickets** — sold, paid, comps, scanned attendance
4. **money** — gross, net, guarantee, costs, settlement/promoter contribution

Minimum useful dataset: ~50 events. Strong dataset: 250+. Research-grade: 1,000+.

## What we do NOT want

- customer / buyer PII (names, emails, phones, addresses)
- payment-card information
- anything unrelated to event economics

## How your data stays private

- Your files are ingested into an **isolated** database, never the public warehouse.
- PII columns are **detected and quarantined** — their values are never read.
- Default sharing policy is `PRIVATE_ONLY`. Nothing is pooled without your explicit opt-in.
- Everything runs **locally**. Nothing is sent to a hosted LLM.
- You can re-run the import and preview anywhere, on your own machine.

## What you get back

- **Portfolio audit** — a structural quality report of your history
- **Artist history analysis** — repeat bookings, billing trajectory, market recurrence
- **Venue benchmarking** — how your venues compare on capacity/attendance evidence
- **Market benchmarking** — market-level event density and repeat behavior
- **Comparable-event research** — historically similar events and their observed outcomes
- **Historical economics summary** — gross/tickets/attendance coverage, not a forecast
- **Buyer-workbench access** — your history loaded into the planning workspace
- **Experimental research outputs** — research-only, clearly separated from commercial data

## What we do NOT promise

- higher profit
- accurate guarantees
- booking recommendations
- prediction accuracy

The product is an **information and research workstation**, not an optimizer or
a recommender.

## Why your history matters

Public data covers *who played and where*. It rarely covers *what a show
actually made*. Your settlement history is the missing piece that turns the
system from a catalog into a financial research tool — for you first, because
your own history benchmarks your own market, venue, and artist decisions.

> The readiness tiers are `STRUCTURAL_ONLY → RETROSPECTIVE_RESEARCH_USABLE →
> ECONOMICS_USABLE → UNDERWRITING_RESEARCH_CANDIDATE`. Row count alone never
> advances a tier; label families and PIT cutoffs do.
