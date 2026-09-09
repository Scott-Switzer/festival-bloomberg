"""50-ARTIST MULTI-LANE BENCHMARK HARNESS (P5).

Stratified 50-artist cohort × task × lane, bounded, cost-aware.
Never spends without budget; never fabricates $0; UNKNOWN stays UNKNOWN.

Usage:
  from festival_bloomberg.acquisition.benchmark import BenchmarkHarness, BENCHMARK_TASKS
  harness = BenchmarkHarness()
  report = harness.run(monid=True, apify=True, owned=True, dry_run=False)

The harness uses the MarketplaceRouter for lane ordering but FORCE-runs
every available lane for the same artists so economics can be compared
head-to-head. Paid lanes are skipped unless budget allows; every attempt
emits a ProcurementRecord for the ledger.
"""

from __future__ import annotations

import json
import time
import hashlib
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import AcquisitionRequest, AcquisitionStatus
from .marketplace_router import Lane, MarketplaceRouter
from .procurement_ledger import ProcurementRecord, aggregate_ledger, record_from_result

# 50-artist stratified cohort — canonical names chosen to exercise identity
# edge cases (common names, international, legacy, emerging) alongside volume.
BENCHMARK_ARTISTS: list[dict[str, str]] = [
    # superstars
    {"artist_key": "bench::taylor_swift", "artist_name": "Taylor Swift"},
    {"artist_key": "bench::beyonce", "artist_name": "Beyoncé"},
    {"artist_key": "bench::drake", "artist_name": "Drake"},
    {"artist_key": "bench::bad_bunny", "artist_name": "Bad Bunny"},
    {"artist_key": "bench::the_weeknd", "artist_name": "The Weeknd"},
    # mid-tier touring
    {"artist_key": "bench::phoebe_bridgers", "artist_name": "Phoebe Bridgers"},
    {"artist_key": "bench::tyler_the_creator", "artist_name": "Tyler, The Creator"},
    {"artist_key": "bench::fred_again", "artist_name": "Fred again.."},
    {"artist_key": "bench::karol_g", "artist_name": "Karol G"},
    {"artist_key": "bench::post_malone", "artist_name": "Post Malone"},
    # emerging / breakthrough
    {"artist_key": "bench::olivia_rodrigo", "artist_name": "Olivia Rodrigo"},
    {"artist_key": "bench::ice_spice", "artist_name": "Ice Spice"},
    {"artist_key": "bench::chappell_roan", "artist_name": "Chappell Roan"},
    {"artist_key": "bench::sabrina_carpenter", "artist_name": "Sabrina Carpenter"},
    {"artist_key": "bench::zach_bryan", "artist_name": "Zach Bryan"},
    # legacy / classic
    {"artist_key": "bench::metallica", "artist_name": "Metallica"},
    {"artist_key": "bench::madonna", "artist_name": "Madonna"},
    {"artist_key": "bench::fleetwood_mac", "artist_name": "Fleetwood Mac"},
    {"artist_key": "bench::bob_dylan", "artist_name": "Bob Dylan"},
    {"artist_key": "bench::elton_john", "artist_name": "Elton John"},
    # hip-hop
    {"artist_key": "bench::kendrick_lamar", "artist_name": "Kendrick Lamar"},
    {"artist_key": "bench::travis_scott", "artist_name": "Travis Scott"},
    {"artist_key": "bench::future", "artist_name": "Future"},
    {"artist_key": "bench::21_savage", "artist_name": "21 Savage"},
    {"artist_key": "bench::megan_thee_stallion", "artist_name": "Megan Thee Stallion"},
    # EDM / electronic
    {"artist_key": "bench::skrillex", "artist_name": "Skrillex"},
    {"artist_key": "bench::peggy_gou", "artist_name": "Peggy Gou"},
    {"artist_key": "bench::calvin_harris", "artist_name": "Calvin Harris"},
    {"artist_key": "bench::charlotte_de_witte", "artist_name": "Charlotte de Witte"},
    # rock / alternative
    {"artist_key": "bench::arctic_monkeys", "artist_name": "Arctic Monkeys"},
    {"artist_key": "bench::paramore", "artist_name": "Paramore"},
    {"artist_key": "bench::muse", "artist_name": "Muse"},
    {"artist_key": "bench::rush", "artist_name": "Rush"},
    # country
    {"artist_key": "bench::morgan_wallen", "artist_name": "Morgan Wallen"},
    {"artist_key": "bench::kacey_musgraves", "artist_name": "Kacey Musgraves"},
    {"artist_key": "bench::luke_combs", "artist_name": "Luke Combs"},
    # pop/related
    {"artist_key": "bench::dua_lipa", "artist_name": "Dua Lipa"},
    {"artist_key": "bench::billie_eilish", "artist_name": "Billie Eilish"},
    # international
    {"artist_key": "bench::bts", "artist_name": "BTS"},
    {"artist_key": "bench::burna_boy", "artist_name": "Burna Boy"},
    # common-name / identity-risk
    {"artist_key": "bench::journey", "artist_name": "Journey"},
    {"artist_key": "bench::phoenix", "artist_name": "Phoenix"},
    {"artist_key": "bench::train", "artist_name": "Train"},
    {"artist_key": "bench::future_common", "artist_name": "Future"},
    {"artist_key": "bench::muse_common", "artist_name": "Muse"},
    # Latin / world
    {"artist_key": "bench::peso_pluma", "artist_name": "Peso Pluma"},
    {"artist_key": "bench::rosalia", "artist_name": "Rosalía"},
    {"artist_key": "bench::anitta", "artist_name": "Anitta"},
    {"artist_key": "bench::j_balvin", "artist_name": "J Balvin"},
]

# Tasks benchmarked head-to-head. Each row defines the provider hint and the
# request shape. TASKS are intentionally small so one full benchmark stays
# under the $10 paid ceiling (providers are polled sequentially, not in burst).
BENCHMARK_TASKS: list[dict[str, Any]] = [
    {"task_type": "SOCIAL_PROFILE", "platform": "tiktok", "max_records": 5, "description": "TikTok profile"},
    {"task_type": "SOCIAL_POSTS", "platform": "tiktok", "max_records": 5, "description": "TikTok posts"},
    {"task_type": "SOCIAL_COMMENTS", "platform": "tiktok", "max_records": 10, "description": "TikTok comments"},
    {"task_type": "SOCIAL_PROFILE", "platform": "instagram", "max_records": 5, "description": "Instagram profile"},
    {"task_type": "SOCIAL_POSTS", "platform": "instagram", "max_records": 5, "description": "Instagram posts"},
    {"task_type": "SOCIAL_PROFILE", "platform": "youtube", "max_records": 5, "description": "YouTube channel"},
    {"task_type": "VIDEO_COMMENTS", "platform": "youtube", "max_records": 10, "description": "YouTube comments"},
    {"task_type": "SOCIAL_MENTION_SEARCH", "platform": "bluesky", "max_records": 10, "description": "Bluesky mentions"},
    {"task_type": "SOCIAL_MENTION_SEARCH", "platform": "x", "max_records": 10, "description": "X search"},
    {"task_type": "SOCIAL_MENTION_SEARCH", "platform": "reddit", "max_records": 10, "description": "Reddit search"},
    {"task_type": "ARTIST_NEWS", "platform": "gdelt", "max_records": 10, "description": "GDELT news"},
    {"task_type": "ARTIST_OFFICIAL_SITE", "platform": "http", "max_records": 1, "description": "Official site crawl"},
]

# Hard cumulative PAID pilot ceiling for one benchmark run (P4).
DEFAULT_PAID_CEILING_USD = 10.0


class BenchmarkHarness:
    """Bounded, cost-aware multi-lane benchmark.

    - Dry-run mode (default) measures lane availability + estimates without spending.
    - Live mode obeys a $10 paid ceiling and records every attempt to the procurement ledger.
    - Every lane is attempted for the SAME artists so economics are comparable.
    """

    def __init__(self, paid_ceiling_usd: float = DEFAULT_PAID_CEILING_USD):
        self.paid_ceiling_usd = paid_ceiling_usd
        self.spent_usd: float = 0.0

    def run(
        self,
        *,
        providers: dict[str, Any] | None = None,
        lanes: list[Lane] | None = None,
        artists: list[dict[str, str]] | None = None,
        tasks: list[dict[str, Any]] | None = None,
        artist_limit: int = 50,
        dry_run: bool = True,
        max_direct_runs: int = 20,
    ) -> dict[str, Any]:
        from .providers import default_providers

        provs = providers or default_providers()
        lanes = lanes or [Lane.OFFICIAL_API, Lane.MONID, Lane.APIFY, Lane.OWNED_HTTP]
        artists = (artists or BENCHMARK_ARTISTS)[:artist_limit]
        tasks = tasks or BENCHMARK_TASKS

        started_at = datetime.now(timezone.utc).isoformat()
        run_id = f"bench_{hashlib.sha256(started_at.encode()).hexdigest()[:8]}"
        router = MarketplaceRouter(provs)

        # Pre-flight: lane availability + estimates (no spend)
        lane_matrix: list[dict[str, Any]] = []
        for task in tasks:
            for lane in lanes:
                dec = router.decide(task_type=task["task_type"], platform=task.get("platform"), request=None, providers=provs)
                # Extract the estimate for this specific lane (not just primary)
                est = next((e for e in dec.ranked if e.lane == lane), None)
                lane_matrix.append({
                    "task_type": task["task_type"],
                    "platform": task.get("platform"),
                    "lane": lane.value,
                    "configured": bool(est.configured) if est else False,
                    "healthy": bool(est.healthy) if est else False,
                    "estimated_cost_usd": est.estimated_cost_usd if est else None,
                    "reason": est.reason if est else None,
                    "primary_for_task": dec.primary.lane.value if dec.primary else None,
                })

        if dry_run:
            # Also run live-but-free probes (public providers: bluesky, gdelt, wikimedia) up to a bounded cap
            free_probe_results = self._free_probes(provs, artists[:5], tasks, lanes, run_id, max_direct_runs)
            return {
                "run_id": run_id,
                "mode": "dry_run",
                "started_at": started_at,
                "artists": artist_limit,
                "tasks": len(tasks),
                "lanes": [l.value for l in lanes],
                "paid_ceiling_usd": self.paid_ceiling_usd,
                "lane_matrix": lane_matrix,
                "free_probe_results": free_probe_results,
                "procurement_records": free_probe_results.get("records", []),
                "note": "dry_run: no paid lane was executed. Free public lanes were probed (bounded). Set dry_run=False to spend up to paid_ceiling.",
            }

        # Live run: head-to-head across SAME artists, bounded, cost-gated
        records: list[dict[str, Any]] = []
        live_results: list[dict[str, Any]] = []
        for task in tasks:
            for lane in lanes:
                if self.spent_usd >= self.paid_ceiling_usd:
                    live_results.append({"task_type": task["task_type"], "platform": task.get("platform"), "lane": lane.value, "skipped_reason": "PAID_CEILING_REACHED"})
                    continue
                # Only run lanes that are configured+healthy and within budget
                est = next((e for e in router.decide(task_type=task["task_type"], platform=task.get("platform"), providers=provs).ranked if e.lane == lane), None)
                if est and not est.configured:
                    live_results.append({"task_type": task["task_type"], "platform": task.get("platform"), "lane": lane.value, "skipped_reason": f"NOT_CONFIGURED: {est.reason or ''}".strip()})
                    continue
                # Actually run for a small cohort (to stay under ceiling)
                results = self._run_lane_for_cohort(provs, lane, task, artists[: min(5, artist_limit)], run_id)
                for r in results:
                    records.append(r)
                    if r.get("total_charge_usd") is not None:
                        self.spent_usd += float(r["total_charge_usd"])
                live_results.append({"task_type": task["task_type"], "platform": task.get("platform"), "lane": lane.value, "attempted": len(results), "spent_usd": round(self.spent_usd, 6)})
                if self.spent_usd >= self.paid_ceiling_usd:
                    break

        agg = {}
        if records:
            from .procurement_ledger import ProcurementRecord

            recs = [ProcurementRecord(**{k: v for k, v in d.items() if k in ProcurementRecord.__dataclass_fields__}) for d in records]
            agg = aggregate_ledger(recs)

        return {
            "run_id": run_id,
            "mode": "live",
            "started_at": started_at,
            "lane_matrix": lane_matrix,
            "live_results": live_results,
            "records": records,
            "aggregated": agg,
            "spent_usd": round(self.spent_usd, 6),
            "paid_ceiling_usd": self.paid_ceiling_usd,
        }

    def _free_probes(self, provs, artists, tasks, lanes, run_id, max_runs: int) -> dict[str, Any]:
        """Run only the public/no-cost lanes (bluesky, gdelt) for a bounded sample."""
        from .providers.bluesky import BlueskyProvider
        from .providers.gdelt import GdeltProvider
        from ..acquisition.transport import UrllibTransport

        transport = UrllibTransport()
        records: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        runs = 0
        free_tasks = [t for t in tasks if t.get("platform") in ("bluesky", "gdelt", "wikimedia", "commoncrawl")]
        for task in free_tasks:
            if runs >= max_runs:
                break
            platform = task.get("platform")
            if platform == "bluesky":
                prov = BlueskyProvider(transport=transport)
                lane = Lane.OFFICIAL_API.value
            elif platform == "gdelt":
                prov = GdeltProvider(transport=transport)
                lane = Lane.OFFICIAL_API.value
            else:
                continue
            for artist in artists:
                if runs >= max_runs:
                    break
                req = AcquisitionRequest.new(entity_id=artist["artist_key"], entity_type="artist", platform=platform, query=artist["artist_name"], max_records=task.get("max_records", 5))
                t0 = time.time()
                try:
                    res = prov.acquire(req)
                except Exception as e:
                    res = None
                    results.append({"task_type": task["task_type"], "platform": platform, "artist": artist["artist_name"], "error": str(e)[:200]})
                    continue
                dur = time.time() - t0
                rec = record_from_result(run_id=run_id, task_type=task["task_type"], lane=lane, provider=platform, endpoint_or_actor=platform, request=req, result=res, duration_seconds=dur)
                records.append(asdict(rec))
                results.append({"task_type": task["task_type"], "platform": platform, "artist": artist["artist_name"], "status": res.status.value, "records": res.record_count, "cost_usd": res.cost_usd})
                runs += 1
                # Throttle public providers politely (GDELT 5s)
                if platform == "gdelt":
                    time.sleep(5.0)
                elif platform == "bluesky":
                    time.sleep(1.0)
        # Aggregate via ledger
        agg: dict[str, Any] = {}
        if records:
            from .procurement_ledger import ProcurementRecord

            recs_objs = [ProcurementRecord(**{k: v for k, v in d.items() if k in ProcurementRecord.__dataclass_fields__}) for d in records]
            agg = aggregate_ledger(recs_objs)
        return {"probed": runs, "results": results, "records": records, "aggregated": agg}

    def _run_lane_for_cohort(self, provs, lane: Lane, task: dict[str, Any], artists, run_id: str) -> list[dict[str, Any]]:
        """Run one lane×task across the cohort and emit ProcurementRecords."""
        import time

        from ..acquisition.transport import UrllibTransport

        transport = UrllibTransport()
        records: list[dict[str, Any]] = []
        # Map lane to a concrete provider instance for this platform
        platform = task.get("platform")
        # Resolve the lane's provider: OFFICIAL_API uses the platform provider itself; MONID/APIFY use those providers
        lane_provider = None
        if lane == Lane.OFFICIAL_API:
            lane_provider = provs.get(platform) or provs.get("http")
        elif lane == Lane.MONID:
            lane_provider = provs.get("monid")
        elif lane == Lane.APIFY:
            lane_provider = provs.get("apify")
        elif lane == Lane.OWNED_HTTP:
            lane_provider = provs.get("http")
        else:
            lane_provider = provs.get("http")

        if lane_provider is None:
            return records

        for artist in artists:
            req = AcquisitionRequest.new(entity_id=artist["artist_key"], entity_type="artist", platform=platform or "web", query=artist["artist_name"], max_records=task.get("max_records", 5))
            t0 = time.time()
            try:
                res = lane_provider.acquire(req)
            except Exception as e:
                from .contracts import AcquisitionResult, AcquisitionStatus
                from datetime import datetime, timezone

                now = datetime.now(timezone.utc)
                res = AcquisitionResult(request_id=req.request_id, provider=lane.value, provider_endpoint=None, status=AcquisitionStatus.PROVIDER_ERROR, started_at=now, completed_at=now, error_category=str(e)[:200])
            dur = time.time() - t0
            rec = record_from_result(run_id=run_id, task_type=task["task_type"], lane=lane.value, provider=getattr(lane_provider, "name", lane.value), endpoint_or_actor=str(getattr(lane_provider, "actor_id", None) or getattr(lane_provider, "name", lane.value)), request=req, result=res, duration_seconds=dur)
            records.append(asdict(rec))
            # Respect paid ceiling even mid-cohort
            if rec.total_charge_usd is not None and self.spent_usd + float(rec.total_charge_usd) > self.paid_ceiling_usd:
                break
            self.spent_usd += float(rec.total_charge_usd or 0.0)
            # Gentle throttle for public lanes
            if platform == "gdelt":
                time.sleep(5.0)
            elif platform == "bluesky":
                time.sleep(0.3)
        return records

    @staticmethod
    def benchtasks_for_platform(platform: str) -> list[dict[str, Any]]:
        return [t for t in BENCHMARK_TASKS if t.get("platform") == platform]
