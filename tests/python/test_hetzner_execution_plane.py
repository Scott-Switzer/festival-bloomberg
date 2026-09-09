"""HETZNER_EXECUTION_PLANE_V1 + SELF_HOSTED_INFERENCE_V1 — unit contract."""

from __future__ import annotations

import os

import pytest

from festival_bloomberg.acquisition.hetzner_broker import (
    CostGuardrail,
    HetznerExecutionBroker,
    ProvisioningStatus,
    WorkerSpec,
)
from festival_bloomberg.acquisition.hetzner_lease import (
    LeaseLedger,
    LeaseRequest,
    sign_lease,
    verify_lease,
)
from festival_bloomberg.inference.cascade import InferenceCascade
from festival_bloomberg.inference.router import InferenceRouter, InferenceLane


# ── Hetzner broker (no credentials — must not throw, must report BLOCKED) ──

def test_hetzner_broker_blocked_without_token(monkeypatch):
    for k in ("HETZNER_API_TOKEN", "HETZNER_TOKEN", "HCLOUD_TOKEN", "HETZNER_CLOUD_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    b = HetznerExecutionBroker(dry_run=True)
    assert b.token_present() is False
    assert b.probe_auth()["status"] == "BLOCKED_BY_CREDENTIAL"
    disc = b.discover_capacity()
    assert disc["status"] == "BLOCKED_BY_CREDENTIAL"
    assert b.robot_present() is False


def test_hetzner_broker_cost_guardrail_blocks(monkeypatch):
    for k in ("HETZNER_API_TOKEN", "HETZNER_TOKEN", "HCLOUD_TOKEN", "HETZNER_CLOUD_TOKEN"):
        monkeypatch.setenv(k, "fake-token-for-test-0000000000000000")
    g = CostGuardrail(max_usd=10.0, spent_usd=9.9)
    b = HetznerExecutionBroker(guardrail=g, dry_run=True)
    spec = WorkerSpec(server_type="cpx32", location="nbg1", image="ubuntu-24.04", ttl_minutes=120)
    # cpx32 ~26.9 EUR/mo -> ~0.0368 EUR/h -> ~0.07 EUR for 2h -> well within $10, so provision succeeds as DRY_RUN
    res = b.create_worker(spec)
    assert res.status == ProvisioningStatus.DRY_RUN
    # Now spend the budget and retry — a 30-day TTL would exceed it
    g.spent_usd = 9.99
    long_spec = WorkerSpec(server_type="cpx32", location="nbg1", image="ubuntu-24.04", ttl_minutes=60 * 24 * 30)
    res2 = b.create_worker(long_spec)
    # 30 days of cpx32 exceeds $10? monthly cap is ~26.9 EUR -> ~$29, so yes, BLOCKED_BY_BUDGET
    assert res2.status == ProvisioningStatus.BLOCKED_BY_BUDGET


def test_hetzner_broker_dry_run_labels(monkeypatch):
    monkeypatch.setenv("HETZNER_API_TOKEN", "fake-token-for-test-0000000000000000")
    b = HetznerExecutionBroker(dry_run=True)
    spec = WorkerSpec(server_type="cpx22", location="hel1", image="ubuntu-24.04", labels={"job_id": "test-job"}, ttl_minutes=60)
    res = b.create_worker(spec, git_sha="abc123def456")
    assert res.status == ProvisioningStatus.DRY_RUN
    assert res.labels["project"] == "festival-intelligence"
    assert res.labels["job_id"] == "test-job"
    assert "expires_at" in res.labels


def test_hetzner_broker_reaper_no_token(monkeypatch):
    for k in ("HETZNER_API_TOKEN", "HETZNER_TOKEN", "HCLOUD_TOKEN", "HETZNER_CLOUD_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    b = HetznerExecutionBroker(dry_run=True)
    res = b.reap_expired_workers()
    assert res["status"] == "BLOCKED_BY_CREDENTIAL"


def test_hetzner_lease_hmac_roundtrip(monkeypatch):
    monkeypatch.delenv("FI_BATCH_HMAC_SECRET", raising=False)
    monkeypatch.delenv("FI_LISTENER_HMAC_SECRET", raising=False)
    # No secret → empty token, verify fails (fail closed)
    assert sign_lease("w", "j", "2026-09-09T10:00:00Z", "lease_abc") == ""
    assert verify_lease("w", "j", "2026-09-09T10:00:00Z", "lease_abc", "") is False
    assert verify_lease("w", "j", "2026-09-09T10:00:00Z", "lease_abc", "bad") is False
    monkeypatch.setenv("FI_BATCH_HMAC_SECRET", "test-secret-32-bytes-long-xxxxxxxx")
    tok = sign_lease("worker1", "job1", "2026-09-09T10:00:00Z", "lease_abc")
    assert len(tok) == 64
    assert verify_lease("worker1", "job1", "2026-09-09T10:00:00Z", "lease_abc", tok) is True
    assert verify_lease("worker1", "job1", "2026-09-09T10:00:00Z", "lease_abc", "0" * 64) is False
    assert verify_lease("worker2", "job1", "2026-09-09T10:00:00Z", "lease_abc", tok) is False


def test_hetzner_lease_ledger_grant(monkeypatch):
    monkeypatch.setenv("FI_BATCH_HMAC_SECRET", "test-secret-32-bytes-long-xxxxxxxx")
    ledger = LeaseLedger()
    grant = ledger.grant(LeaseRequest(worker_id="w1", capabilities=("scraper",)), {"job_id": "j1", "job_type": "heavy_batch"})
    assert grant.worker_id == "w1"
    assert grant.job_id == "j1"
    assert ledger.verify_callback(grant.lease_id, "w1", grant.token) is True
    assert ledger.verify_callback(grant.lease_id, "w2", grant.token) is False


# ── Inference router + cascade ─────────────────────────────────────


def test_inference_router_prefers_nim_for_embed():
    r = InferenceRouter(nim_available=True, nim_model="nvidia/nemotron-3-embed-1b", hetzner_cpu_available=False)
    d = r.decide("EMBED")
    assert d.primary == InferenceLane.NIM_FREE


def test_inference_router_falls_back_to_hetzner_when_nim_down():
    r = InferenceRouter(nim_available=False, hetzner_cpu_available=True, hetzner_cpu_model="local-minilm")
    d = r.decide("SENTIMENT")
    assert d.primary == InferenceLane.HETZNER_CPU


def test_inference_router_unavailable_when_no_lane():
    r = InferenceRouter(nim_available=False, hetzner_cpu_available=False, deterministic_available=False)
    d = r.decide("EMBED")
    assert d.primary == InferenceLane.UNAVAILABLE


def test_cascade_deterministic_gate():
    r = InferenceRouter(nim_available=True, nim_model="nvidia/nemotron-3.5-lightning-30b-a3b")
    c = InferenceCascade(router=r)
    assert c.classify("", task="SENTIMENT").lane == InferenceLane.DETERMINISTIC
    assert c.classify("https://example.com", task="SENTIMENT").lane == InferenceLane.DETERMINISTIC
    # short text
    assert c.classify("hi", task="SENTIMENT").lane == InferenceLane.DETERMINISTIC


def test_cascade_escalates_to_nim_when_local_low_confidence():
    r = InferenceRouter(nim_available=True, nim_model="nvidia/nemotron-3.5-lightning-30b-a3b", hetzner_cpu_available=True, hetzner_cpu_model="local-model")
    c = InferenceCascade(router=r, local_confidence_threshold=0.78)

    def low_local(text, task):
        return ("POSITIVE", 0.5)

    def nim_caller(text, task):
        return ("POSITIVE", 0.92, "nvidia/nemotron-3.5-lightning-30b-a3b")

    res = c.classify("This show was incredible!", task="SENTIMENT", local_scorer=low_local, nim_caller=nim_caller)
    assert res.lane == InferenceLane.NIM_FREE
    assert res.escalated is True


def test_cascade_accepts_local_when_confident():
    r = InferenceRouter(nim_available=True, nim_model="nvidia/nemotron-3.5-lightning-30b-a3b", hetzner_cpu_available=True, hetzner_cpu_model="local-model")
    c = InferenceCascade(router=r, local_confidence_threshold=0.78)

    def high_local(text, task):
        return ("NEGATIVE", 0.95)

    res = c.classify("Terrible sound, left early.", task="SENTIMENT", local_scorer=high_local, nim_caller=None)
    assert res.lane == InferenceLane.HETZNER_CPU
    assert res.escalated is False
    assert res.label == "NEGATIVE"
