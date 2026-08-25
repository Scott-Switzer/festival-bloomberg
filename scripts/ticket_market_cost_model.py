"""Measured cost model for the REAL_TICKET_MARKET_RAIL_V1 ticket-market rail.

Uses ACTUAL measured per-run economics from the live probes (2026-08-25):

  * SeatGeek (axlymxp~seatgeek-event-scraper):
      - advertised $5.00 / 1,000 dataset items
      - real probe: 100 records returned per run despite maxItems=5 (the actor
        ignores maxItems; the dataset fetch caps at 100). Each run has a
        minimum charge (pay-per-event pricing); two runs consumed ~$0.90.
      - CRITICAL: actor ignores searchQuery/city/state/date filters — returns a
        country-wide homepage feed. Not usable for targeted event observation.

  * The account used (single secondary credential) hit its $5/mo free-tier
    limit after a handful of runs. Monthly billing cycle, ~Sept 1 reset.

Cost scenarios below are computed from measured per-run economics where the
source is operational, and marked BLOCKED where it is not.

DISCLAIMER: listing_count / ticket_count are marketplace availability PROXIES.
Never treat them as tickets sold.
"""

from __future__ import annotations

import json

# Measured economics (2026-08-25 probes).
MEASURED = {
    "seatgeek_axlymxp": {
        "actor": "axlymxp~seatgeek-event-scraper",
        "status": "FILTERS_BROKEN",           # ignores searchQuery/city/date
        "advertised_per_1k": 5.00,
        "records_per_run": 100,               # observed (dataset fetch cap)
        "min_charge_per_run": 0.45,           # observed (~$0.90 for 2 runs)
        "usable_for_targeted": False,
        "notes": "Returns homepage feed regardless of filters. NOT usable for watch-universe targeted observation.",
    },
    "seatgeek_crawlerbros": {
        "actor": "crawlerbros~seatgeek-scraper",
        "status": "FILTERS_BROKEN",
        "advertised_per_1k": None,            # PAY_PER_EVENT
        "records_per_run": 20,                # observed (default maxItems)
        "min_charge_per_run": 0.45,           # observed
        "usable_for_targeted": False,
        "notes": "Echoes default input (searchQuery='New York Yankees'). Not usable for targeted observation.",
    },
    "vividseats_hoholabs": {
        "actor": "hoholabs~vividseats-scraper",
        "status": "UNVERIFIED_TARGETED",
        "advertised_per_1k": None,
        "records_per_run": 10,                # bakeoff
        "min_charge_per_run": None,
        "usable_for_targeted": "NOT_VERIFIED",
        "notes": "Supports q= search in schema; targeted test not run (budget exhausted). Prior bakeoff returned parking events.",
    },
    "stubhub_lentic": {
        "actor": "lentic_clockss~stubhub-scraper",
        "status": "RUN_FAILED_BAKEOFF",
        "advertised_per_1k": None,
        "records_per_run": 0,
        "min_charge_per_run": None,
        "usable_for_targeted": False,
        "notes": "Bakeoff run failed (RUN_FAILED). Mode/input requires debugging.",
    },
    "gametime_lexis": {
        "actor": "lexis-solutions~gametime-scraper",
        "status": "URL_ONLY",
        "advertised_per_1k": 1.90,
        "records_per_run": None,
        "min_charge_per_run": None,
        "usable_for_targeted": "URL_ONLY",
        "notes": "startUrls-only actor. No search query; needs event URLs.",
    },
    "tickpick_automation": {
        "actor": "automation-lab~tickpick-events-tickets-scraper",
        "status": "URL_OR_QUERY",
        "advertised_per_1k": None,
        "records_per_run": None,
        "min_charge_per_run": None,
        "usable_for_targeted": "NOT_VERIFIED",
        "notes": "Supports searchQueries; not executed this session.",
    },
}


def scenario(events: int, obs_per_day: int, days: int, cost_per_event_per_obs: float) -> dict:
    obs = events * obs_per_day * days
    cost = events * obs_per_day * days * cost_per_event_per_obs
    return {
        "events": events,
        "observations_per_day": obs_per_day,
        "days": days,
        "total_observations": obs,
        "cost_usd": round(cost, 2),
    }


def main() -> None:
    print("=" * 72)
    print("TICKET-MARKET RAIL — MEASURED COST MODEL (2026-08-25)")
    print("=" * 72)
    print("\n## Measured source economics\n")
    for key, m in MEASURED.items():
        print(f"- **{key}** ({m['actor']})")
        print(f"    status: {m['status']} | usable_for_targeted: {m['usable_for_targeted']}")
        print(f"    advertised: ${m['advertised_per_1k']}/1K | observed records/run: {m['records_per_run']} | min charge/run: ${m['min_charge_per_run']}")
        print(f"    {m['notes']}")

    print("\n## Scenario costs (per month, single source)\n")
    # APPROACH A — targeted per-event query (what the rail was designed for):
    # the current SeatGeek actor returns ~100 records/run regardless of maxItems,
    # so each per-event run costs ~$0.45-0.50 (measured). Uneconomical.
    per_event_obs = 0.45  # measured minimum charge per actor run
    for label, ev in [("LEAN", 100), ("STANDARD", 100), ("DENSE", 100)]:
        freq = 1 if label == "LEAN" else 2 if label == "STANDARD" else 3
        s = scenario(ev, freq, 30, per_event_obs)
        print(f"  A-{label:6s} per-event query: {ev} events x {freq}/day x 30d = {s['total_observations']:>7d} obs | ${s['cost_usd']:>9.2f}")

    print("\n  Scaled universes — per-event query (daily, once/day, 30d):")
    for ev in (100, 500, 1000):
        s = scenario(ev, 1, 30, per_event_obs)
        print(f"    {ev:>5d} events daily: ${s['cost_usd']:>8.2f}/mo  ({s['total_observations']} observations)")

    # APPROACH B — market-level sweep (one run per market, then resolve):
    # 6 markets → ~6 runs/wave, ~100 records each at $5/1K ≈ $0.50/run ≈ $3/wave.
    # Covers the whole 100-event universe in one pass. This is the realistic
    # path given the actors ignore targeted filters.
    sweep_runs = 6  # one per market (LA/NY/CHI/LV/NSH/DAL)
    sweep_per_run = 0.50
    print("\n  B-MARKET SWEEP (one run per market, resolve locally):")
    for freq in (1, 2, 3):
        cost = sweep_runs * sweep_per_run * freq * 30
        print(f"    {freq}x/day: {sweep_runs * freq * 30:>6d} runs/mo | ${cost:>8.2f}/mo")

    print("\n## Account constraint")
    print("  Single credential: FREE tier $5.00/mo hard limit.  ")
    print("  Measured: 2 SeatGeek runs consumed ~$0.90; the credential hit its")
    print("  $5 limit after a handful of runs this cycle (multiple actors ran earlier).")
    print("  => A targeted 100-event daily rail at $0.45/event/run would exceed the")
    print("     free tier within ~11 runs. Paid plan or cheaper per-run sources required.")
    print("  => The SeatGeek actors also ignore filters, so even paying does not")
    print("     currently enable targeted watch-universe observation.")

    print("\n## Storage growth (approx)")
    per_obs_kb = 1.5  # raw payload + normalized + metadata
    for ev, freq in [(100, 1), (100, 2), (500, 1)]:
        obs = ev * freq * 30
        print(f"  {ev} events x {freq}/day: {obs} obs/mo ≈ {obs * per_obs_kb / 1024:.1f} MB/mo")

    out = {
        "measured": MEASURED,
        "scenarios": {
            "per_event_query": {
                "lean": scenario(100, 1, 30, per_event_obs),
                "standard": scenario(100, 2, 30, per_event_obs),
                "dense": scenario(100, 3, 30, per_event_obs),
            },
            "market_sweep": {
                "runs_per_wave": sweep_runs,
                "cost_per_run": sweep_per_run,
                "monthly_at_1x": round(sweep_runs * sweep_per_run * 30, 2),
                "monthly_at_2x": round(sweep_runs * sweep_per_run * 2 * 30, 2),
                "monthly_at_3x": round(sweep_runs * sweep_per_run * 3 * 30, 2),
            },
        },
        "account": {
            "plan": "FREE",
            "monthly_limit_usd": 5.00,
            "measured_runs_per_month_before_cap": "~11 targeted runs at $0.45",
            "reset": "monthly billing cycle (~Sept 1)",
        },
        "disclaimer": "listing_count/ticket_count are availability proxies, never tickets sold.",
    }
    Path = __import__("pathlib").Path
    out_path = Path(__file__).resolve().parents[1] / "data" / "workspace" / "ticket_market_cost_model.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
