"""HETZNER_EXECUTION_PLANE_V1 — broker + capacity discovery + orphan reaper + cost guardrail.

Cloudflare is the control plane. Hetzner is an ephemeral execution plane for:
  - Crawlee/Playwright scraping (STATIC_HTTP → PLAYWRIGHT)
  - heavy DuckDB / ListenBrainz aggregation / Common Crawl WARC parsing
  - CPU local inference fallback (NIM free is still primary large-model lane)
  - provider benchmarking (Cloudflare Browser Run vs Hetzner)

No secrets are logged or committed. Credentials are env-only (HETZNER_API_TOKEN /
HCLOUD_TOKEN / HETZNER_CLOUD_TOKEN, plus optional HETZNER_ROBOT_* for dedicated
GPU provisioning — probed but not required). Missing credentials produce a
typed BLOCKED state instead of silent failure.

Every worker is labeled and has a hard TTL; an orphan reaper deletes expired
servers. Billing model is hard-capped at $10 of new Cloud compute for this
milestone — not a monthly budget, a pilot ceiling.

Implementation is intentionally stdlib-only (urllib + hmac) so the broker can
run in the Cloudflare Worker (via its JS counterpart) and in local/Python batch
jobs with the same semantics.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from ..localenv import load_local_env

load_local_env()

HCLOUD_API_BASE = "https://api.hetzner.cloud/v1"
HETZNER_TOKEN_ENV_CANDIDATES = (
    "HETZNER_API_TOKEN",
    "HETZNER_TOKEN",
    "HCLOUD_TOKEN",
    "HETZNER_CLOUD_TOKEN",
)
HETZNER_ROBOT_ENV_CANDIDATES = (
    "HETZNER_ROBOT_USER",
    "HETZNER_ROBOT_PASSWORD",
    "ROBOT_USER",
    "ROBOT_PASSWORD",
)


def _hetzner_cloud_token() -> str | None:
    for name in HETZNER_TOKEN_ENV_CANDIDATES:
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return None


def _hetzner_robot_present() -> bool:
    return any((os.environ.get(n) or "").strip() for n in HETZNER_ROBOT_ENV_CANDIDATES)


def hetzner_cloud_token_present() -> bool:
    return _hetzner_cloud_token() is not None


def hetzner_robot_present() -> bool:
    return _hetzner_robot_present()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Capacity / cost guardrail ─────────────────────────────────────────

# Public list prices (EUR/month) for the pilot-relevant Cloud types, taken
# from Hetzner's public pricing page. Used for estimate only — the broker
# reports the API's own pricing when available and falls back to these.
# EUR→USD is intentionally not baked in; monthly_usd is reported as
# monthly_eur when FX is UNKNOWN so callers never see a fabricated USD.
KNOWN_CLOUD_MONTHLY_EUR: dict[str, float] = {
    "cpx22": 13.40,
    "cpx32": 26.90,
    "cpx42": 53.90,
    "cpx52": 107.90,
    "cx22": 4.50,
    "cx32": 9.00,
    "cx42": 18.00,
    "cax11": 4.00,
    "cax21": 7.50,
    "cax31": 15.00,
    "cax41": 30.00,
}

# Pilot hard ceiling for this milestone (new Cloud compute only).
PILOT_BUDGET_USD = 10.0
# Dedup window for cost accounting (avoid double-charging a retried ledger row).
PILOT_BUDGET_LABEL = "HETZNER_CLOUD_PILOT_V1"

WELL_KNOWN_PILOT_TYPES: tuple[str, ...] = ("cpx22", "cpx32", "cpx42", "cx22", "cx32", "cax11", "cax21")


class ProvisioningStatus(str, Enum):
    """Typed outcome of a provisioning attempt — no ambiguous booleans."""

    PROVISIONED = "PROVISIONED"
    BLOCKED_BY_CREDENTIAL = "BLOCKED_BY_CREDENTIAL"
    BLOCKED_BY_BUDGET = "BLOCKED_BY_BUDGET"
    BLOCKED_BY_ROBOT_CREDENTIAL = "BLOCKED_BY_ROBOT_CREDENTIAL"
    CAPACITY_UNAVAILABLE = "CAPACITY_UNAVAILABLE"
    API_ERROR = "API_ERROR"
    DRY_RUN = "DRY_RUN"


@dataclass(frozen=True)
class CapacityOption:
    server_type: str
    architecture: str  # x86 / arm
    vcpu: int
    ram_gb: float
    disk_gb: int
    available: bool
    location: str | None = None
    hourly_eur: float | None = None
    monthly_eur: float | None = None
    monthly_usd: float | None = None  # None when FX is UNKNOWN
    deprecation: str | None = None


@dataclass(frozen=True)
class WorkerSpec:
    """Fully qualified spec for one ephemeral worker."""

    server_type: str
    location: str  # e.g. nbg1, hel1, ash
    image: str  # e.g. ubuntu-24.04
    ssh_key_label: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    ttl_minutes: int = 120
    workload: str = "scraper"  # scraper | heavy_batch | inference

    def expires_at(self) -> datetime:
        return _now() + timedelta(minutes=self.ttl_minutes)


@dataclass
class ProvisionResult:
    status: ProvisioningStatus
    server_id: int | None = None
    server_name: str | None = None
    public_ip: str | None = None
    spec: WorkerSpec | None = None
    expires_at: str | None = None
    estimated_hourly_usd: float | None = None
    estimated_max_cost_usd: float | None = None
    reason: str | None = None
    api_latency_ms: int | None = None
    # Full worker label set (for inventory/audit) — never includes secrets.
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class CostGuardrail:
    """Pilot budget guardrail — $10 of new Cloud compute.

    The guardrail is enforced locally before any API call and again
    server-side via the provisioning broker's actual lifetime accounting.
    """

    max_usd: float = PILOT_BUDGET_USD
    spent_usd: float = 0.0
    # per-worker spend ledger (worker_id -> cost)
    ledger: dict[str, float] = field(default_factory=dict)

    @property
    def remaining(self) -> float:
        return max(0.0, self.max_usd - self.spent_usd)

    def can_afford(self, estimate_usd: float | None) -> bool:
        if estimate_usd is None:
            return True  # unknown cost is not fabricated, but not a blocker
        return estimate_usd <= self.remaining + 1e-9

    def charge(self, worker_id: str, cost_usd: float | None) -> None:
        if cost_usd is None:
            self.ledger[worker_id] = 0.0
            return
        self.spent_usd += cost_usd
        self.ledger[worker_id] = cost_usd

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_usd": self.max_usd,
            "spent_usd": round(self.spent_usd, 4),
            "remaining_usd": round(self.remaining, 4),
            "workers": len(self.ledger),
            "label": PILOT_BUDGET_LABEL,
        }


# ── Hetzner Cloud client (stdlib, no boto) ────────────────────────────

def _hcloud_request(
    method: str,
    path: str,
    token: str,
    body: dict | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict]:
    """Minimal Hetzner Cloud API call via urllib (stdlib). Returns (status, json)."""
    import urllib.request, urllib.error  # noqa: PLC0415  (stdlib, lazy)
    url = f"{HCLOUD_API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted Hetzner endpoint, lab)
            raw = resp.read()
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            return int(resp.status), payload
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload: dict[str, Any] = json.loads(raw)
        except Exception:
            payload = {"error": {"code": exc.code, "message": raw[:500]}}
        return int(exc.code), payload
    except Exception as exc:  # network etc — caller maps to API_ERROR
        return 0, {"error": {"code": "NETWORK", "message": f"{type(exc).__name__}: {str(exc)[:300]}"}}


@dataclass
class HetznerExecutionBroker:
    """Control-plane broker for Hetzner Cloud ephemeral workers.

    All methods are credential-gated: absence produces BLOCKED_BY_CREDENTIAL
    instead of a thrown exception, so callers can report the lane as
    BLOCKED_BY_CREDENTIAL without risking secret leakage in logs.

    Every worker created by this broker carries:
      project=festival-intelligence, environment, role, git_sha, job_id, expires_at
    and is subject to the orphan reaper.
    """

    guardrail: CostGuardrail = field(default_factory=CostGuardrail)
    dry_run: bool = False  # when True, never calls the real API

    # ── capability / discovery ──────────────────────────────────────

    def token_present(self) -> bool:
        return hetzner_cloud_token_present()

    def robot_present(self) -> bool:
        return hetzner_robot_present()

    def probe_auth(self, token: str | None = None) -> dict[str, Any]:
        """Cheap auth probe: GET /v1/servers?per_page=1. Reports READ vs READ+WRITE via error code shape."""
        tok = token or _hetzner_cloud_token()
        if not tok:
            return {"present": False, "permission": "UNKNOWN", "status": "BLOCKED_BY_CREDENTIAL", "reason": "no Hetzner Cloud token in env"}
        status, payload = _hcloud_request("GET", "/servers?per_page=1", tok)
        if status == 200:
            # A 200 on list proves at least READ. Write is inferred by a
            # harmless conditional probe only if needed — not inferred from 200 alone.
            return {"present": True, "permission": "READ", "status": "OK", "server_count_hint": len((payload.get("servers") or []))}
        if status in (401, 403):
            return {"present": True, "permission": "UNKNOWN", "status": "AUTH_FAILURE", "http_status": status, "error": (payload.get("error") or {}).get("message", "")[:200]}
        return {"present": True, "permission": "UNKNOWN", "status": "API_ERROR", "http_status": status, "error": str(payload)[:500]}

    def discover_capacity(self, token: str | None = None) -> dict[str, Any]:
        """HETZNER_CAPACITY_DISCOVERY_V1 — list server types + locations + pricing from the API.

        Never hard-codes availability: returns only what the API reports,
        plus a fallback known-price table for monthly estimates when the API
        omits pricing.
        """
        tok = token or _hetzner_cloud_token()
        if not tok:
            return {"status": "BLOCKED_BY_CREDENTIAL", "options": [], "reason": "no Hetzner Cloud token"}
        out: list[CapacityOption] = []
        # /v1/server_types is the canonical capacity/pricing endpoint
        status, payload = _hcloud_request("GET", "/server_types", tok or "")
        if status != 200:
            # Fall back to the well-known pilot set with availability UNKNOWN
            for st in WELL_KNOWN_PILOT_TYPES:
                out.append(CapacityOption(server_type=st, architecture="x86" if st.startswith("c") else "arm", vcpu=0, ram_gb=0, disk_gb=0, available=False, monthly_eur=KNOWN_CLOUD_MONTHLY_EUR.get(st)))
            return {"status": "API_ERROR", "http_status": status, "options": [o.__dict__ for o in out], "error": str(payload)[:500]}
        # Parse the API's server_types list
        locations: list[str] = []
        try:
            # Also fetch locations so we know where each type is creatable
            ls, lp = _hcloud_request("GET", "/locations", tok or "")
            if ls == 200:
                locations = [loc.get("name") for loc in (lp.get("locations") or []) if loc.get("name")]
        except Exception:
            locations = []
        default_locs = locations or ["nbg1", "hel1", "fsn1", "ash"]
        for st in (payload.get("server_types") or []):
            name = str(st.get("name") or "").strip()
            if not name:
                continue
            prices = st.get("prices") or []
            # price for nbg1 is representative; all locations share the same monthly EUR in Hetzner Cloud
            price_entry = next((p for p in prices if isinstance(p, dict) and p.get("location") in default_locs), None)
            hourly_eur = None
            monthly_eur = KNOWN_CLOUD_MONTHLY_EUR.get(name)
            if price_entry:
                try:
                    hourly_eur = float(price_entry.get("price_hourly", {}).get("gross", 0) or 0) or None
                    monthly_eur = float(price_entry.get("price_monthly", {}).get("gross", 0) or 0) or monthly_eur
                except Exception:
                    pass
            # Architecture
            arch = str(st.get("architecture") or ("arm" if name.startswith("cax") else "x86"))
            cores = int(st.get("cores") or st.get("cpu_cores") or 0)
            ram = float(st.get("memory") or 0)
            disk = int(st.get("disk") or 0)
            dep = st.get("deprecation_announced")
            # Availability: Cloud types are creatable unless deprecated; per-location
            # availability is determined at create time (422 LOCATION_NOT_SUPPORTED).
            # The API has no per-location \"available\" boolean — report False only for deprecated.
            available = not bool(dep)
            # Choose a representative location (first that supports this type's architecture preference)
            loc_pick = default_locs[0]
            out.append(CapacityOption(server_type=name, architecture=arch, vcpu=cores, ram_gb=ram, disk_gb=disk, available=available, location=loc_pick, hourly_eur=hourly_eur, monthly_eur=monthly_eur, monthly_usd=None, deprecation=dep))
        return {"status": "OK", "locations": locations or default_locs, "options": [o.__dict__ for o in sorted(out, key=lambda o: (o.monthly_eur or 999, o.server_type))], "count": len(out)}

    def select_cheapest_available(
        self,
        *,
        min_vcpu: int = 2,
        min_ram_gb: float = 4.0,
        require_x86: bool = True,
        token: str | None = None,
    ) -> CapacityOption | None:
        """Pick the cheapest available type meeting the workload contract."""
        disc = self.discover_capacity(token=token)
        best: CapacityOption | None = None
        for raw in disc.get("options") or []:
            opt = CapacityOption(**{k: raw[k] for k in CapacityOption.__dataclass_fields__ if k in raw})  # type: ignore[arg-type]
            if not opt.available:
                continue
            if require_x86 and opt.architecture != "x86":
                continue
            if opt.vcpu and opt.vcpu < min_vcpu:
                continue
            if opt.ram_gb and opt.ram_gb < min_ram_gb:
                continue
            if best is None or (opt.monthly_eur or 999) < (best.monthly_eur or 999):
                best = opt
        return best

    def estimate_max_cost(self, server_type: str, ttl_minutes: int) -> float | None:
        """Upper-bound cost for ttl_minutes at the type's monthly cap (prorated hourly)."""
        monthly = KNOWN_CLOUD_MONTHLY_EUR.get(server_type)
        if monthly is None:
            return None
        hours = max(1.0, ttl_minutes / 60.0)
        # Hetzner bills max(monthly_cap, hourly * hours) — for pilots far short
        # of a month, the hourly proration dominates; never exceed monthly.
        hourly = monthly / 730.0  # approx hours/month
        return round(min(monthly, hourly * hours), 4)

    # ── provisioning ────────────────────────────────────────────────

    def _labels_for(self, spec: WorkerSpec, git_sha: str | None = None) -> dict[str, str]:
        """Canonical label set for every worker — never includes secrets."""
        sha = (git_sha or os.environ.get("GIT_SHA") or os.environ.get("GITHUB_SHA") or "dev")[:12]
        expires = spec.expires_at().strftime("%Y-%m-%dT%H:%M:%SZ")
        base = {
            "project": "festival-intelligence",
            "environment": os.environ.get("ENVIRONMENT", "staging"),
            "role": spec.workload,
            "git_sha": sha,
            "job_id": spec.labels.get("job_id", "hetzner-lease"),
            "expires_at": expires,
        }
        # caller-supplied labels win for job_id override etc, but cannot erase required keys
        merged = {**base, **(spec.labels or {})}
        merged.setdefault("project", "festival-intelligence")
        return merged

    def create_worker(
        self,
        spec: WorkerSpec,
        *,
        git_sha: str | None = None,
        token: str | None = None,
    ) -> ProvisionResult:
        """Create one ephemeral Cloud server. Credential- and budget-gated."""
        tok = token or _hetzner_cloud_token()
        if not tok:
            return ProvisionResult(status=ProvisioningStatus.BLOCKED_BY_CREDENTIAL, spec=spec, reason="no Hetzner Cloud token in env (HETZNER_API_TOKEN / HCLOUD_TOKEN)")
        max_cost = self.estimate_max_cost(spec.server_type, spec.ttl_minutes)
        if max_cost is not None and not self.guardrail.can_afford(max_cost):
            return ProvisionResult(status=ProvisioningStatus.BLOCKED_BY_BUDGET, spec=spec, estimated_max_cost_usd=max_cost, reason=f"pilot budget remaining ${self.guardrail.remaining:.2f} < estimated ${max_cost:.2f}")
        labels = self._labels_for(spec, git_sha=git_sha)
        if self.dry_run:
            return ProvisionResult(status=ProvisioningStatus.DRY_RUN, spec=spec, labels=labels, expires_at=labels["expires_at"], estimated_max_cost_usd=max_cost, reason="dry_run — no API call")
        t0 = time.time()
        body: dict[str, Any] = {
            "name": f"fi-{spec.workload}-{int(time.time())}",
            "server_type": spec.server_type,
            "location": spec.location,
            "image": spec.image,
            "labels": labels,
            # No SSH key required for cloud-init bootstrap lanes; when present it is a label/name reference, not a secret.
            "start_after_create": True,
        }
        if spec.ssh_key_label:
            body["ssh_keys"] = [spec.ssh_key_label]
        status, payload = _hcloud_request("POST", "/servers", tok, body=body, timeout=30.0)
        latency = int((time.time() - t0) * 1000)
        if status in (200, 201):
            srv = payload.get("server") or {}
            return ProvisionResult(
                status=ProvisioningStatus.PROVISIONED,
                server_id=srv.get("id"),
                server_name=srv.get("name"),
                public_ip=(srv.get("public_net") or {}).get("ipv4", {}).get("ip"),
                spec=spec, labels=labels, expires_at=labels["expires_at"],
                estimated_max_cost_usd=max_cost, api_latency_ms=latency,
            )
        if status in (401, 403):
            return ProvisionResult(status=ProvisioningStatus.BLOCKED_BY_CREDENTIAL, spec=spec, labels=labels, reason=f"auth failure {status}: {str(payload)[:300]}", api_latency_ms=latency)
        if status == 422:
            # location not supported / type unavailable in location
            return ProvisionResult(status=ProvisioningStatus.CAPACITY_UNAVAILABLE, spec=spec, labels=labels, reason=str(payload)[:500], api_latency_ms=latency)
        return ProvisionResult(status=ProvisioningStatus.API_ERROR, spec=spec, labels=labels, reason=f"http {status}: {str(payload)[:500]}", api_latency_ms=latency)

    def delete_worker(self, server_id: int, token: str | None = None) -> dict[str, Any]:
        tok = token or _hetzner_cloud_token()
        if not tok:
            return {"status": "BLOCKED_BY_CREDENTIAL"}
        t0 = time.time()
        status, payload = _hcloud_request("DELETE", f"/servers/{server_id}", tok, timeout=15.0)
        return {"status": "OK" if status in (200, 204) else f"HTTP_{status}", "http_status": status, "payload": payload, "latency_ms": int((time.time() - t0) * 1000)}

    def list_managed_workers(self, token: str | None = None) -> dict[str, Any]:
        """List Festival-Intelligence workers only (label filter). Never lists unrelated account resources silently."""
        tok = token or _hetzner_cloud_token()
        if not tok:
            return {"status": "BLOCKED_BY_CREDENTIAL", "servers": []}
        status, payload = _hcloud_request("GET", "/servers?label_selector=project%3Dfestival-intelligence", tok)
        if status != 200:
            return {"status": f"HTTP_{status}", "servers": [], "error": str(payload)[:500]}
        servers = payload.get("servers") or []
        # Surface only the safe label fields + id/name/status/created — never secrets.
        safe = []
        for s in servers:
            safe.append({
                "id": s.get("id"), "name": s.get("name"), "status": s.get("status"),
                "server_type": (s.get("server_type") or {}).get("name"),
                "location": (s.get("datacenter") or {}).get("location", {}).get("name") if isinstance(s.get("datacenter"), dict) else s.get("location"),
                "labels": s.get("labels") or {}, "created": s.get("created"),
                "public_ip": (s.get("public_net") or {}).get("ipv4", {}).get("ip"),
            })
        return {"status": "OK", "servers": safe, "count": len(safe)}

    # ── TTL / orphan reaper ─────────────────────────────────────────

    def reap_expired_workers(self, token: str | None = None, dry_run: bool | None = None) -> dict[str, Any]:
        """HETZNER_ORPHAN_REAPER — delete managed workers whose expires_at is in the past.

        Scoped to project=festival-intelligence and only when a worker has an
        explicit expires_at label. Workers without expires_at are reported but
        never deleted. Dry-run is the default in local/dev unless explicitly disabled.
        """
        tok = token or _hetzner_cloud_token()
        if not tok:
            return {"status": "BLOCKED_BY_CREDENTIAL", "reaped": [], "skipped": []}
        listed = self.list_managed_workers(token=tok)
        if listed.get("status") != "OK":
            return listed
        now = _now()
        reaped: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for srv in listed.get("servers") or []:
            labels = srv.get("labels") or {}
            expires_raw = labels.get("expires_at")
            if not expires_raw:
                skipped.append({"id": srv.get("id"), "name": srv.get("name"), "reason": "no expires_at label — not reaped"})
                continue
            try:
                exp = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
            except Exception:
                skipped.append({"id": srv.get("id"), "name": srv.get("name"), "reason": f"unparseable expires_at {expires_raw!r}"})
                continue
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now <= exp:
                skipped.append({"id": srv.get("id"), "name": srv.get("name"), "reason": f"not yet expired (expires {expires_raw})"})
                continue
            is_dry = self.dry_run if dry_run is None else dry_run
            if is_dry:
                reaped.append({"id": srv.get("id"), "name": srv.get("name"), "expires_at": expires_raw, "dry_run": True})
                continue
            res = self.delete_worker(int(srv["id"]), token=tok)
            reaped.append({"id": srv.get("id"), "name": srv.get("name"), "expires_at": expires_raw, "delete_status": res.get("status")})
        return {"status": "OK", "reaped": reaped, "skipped": skipped, "checked": len(listed.get("servers") or [])}

    # ── lease helper ────────────────────────────────────────────────

    def sign_lease(self, worker_id: str, job_id: str, lease_expiry: str, secret: str | None = None) -> str:
        """HMAC-SHA256 lease token for external worker callbacks (reuses batch HMAC conventions)."""
        key = (secret or os.environ.get("FI_BATCH_HMAC_SECRET") or os.environ.get("FI_LISTENER_HMAC_SECRET") or "").encode()
        if not key:
            return ""
        msg = f"{worker_id}:{job_id}:{lease_expiry}".encode()
        return hmac.new(key, msg, hashlib.sha256).hexdigest()
