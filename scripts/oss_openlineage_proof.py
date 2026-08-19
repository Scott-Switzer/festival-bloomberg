"""OpenLineage FileTransport proof — no deployed lineage backend required.

Emits START + COMPLETE RunEvents for the Ticketmaster acquisition pilot to a
local file, carrying Festival-specific facets (rights, evidence class,
knowledge_time/PIT semantics, versions, input fingerprint, acquisition run id).

Run:  PYTHONPATH=python .venv/bin/python scripts/oss_openlineage_proof.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from openlineage.client import OpenLineageClient, set_producer
from openlineage.client.run import Dataset, Job, Run, RunEvent, RunState
from openlineage.client.transport import FileTransport
from openlineage.client.transport.file import FileConfig

set_producer("https://github.com/Scott-Switzer/festival-bloomberg")

NAMESPACE = "festival-bloomberg"
RUN_ID = "7b3f2a1c-4d5e-4f6a-9b8c-0d1e2f3a4b5c"
JOB_NAME = "ticketmaster_forward_acquisition"


def _facet(producer: str, schema: str, **data) -> dict:
    """A spec-shaped custom facet: data + provenance envelope."""
    return {"_producer": producer, "_schemaURL": schema, **data}


def festival_run_facets() -> dict:
    return {
        "sourcePolicyStatus": _facet(NAMESPACE, "festival/sourcePolicyStatus/1-0-0", status="RESEARCH_ONLY"),
        "commercialUseStatus": _facet(NAMESPACE, "festival/commercialUseStatus/1-0-0", status="PROTOTYPE_ONLY"),
        "evidenceClass": _facet(NAMESPACE, "festival/evidenceClass/1-0-0", value="EVENT_LISTING"),
        "knowledgeTimeSemantics": _facet(NAMESPACE, "festival/knowledgeTimeSemantics/1-0-0", semantics="retrieval_time", note="event_time != knowledge_time"),
        "pitCutoff": _facet(NAMESPACE, "festival/pitCutoff/1-0-0", cutoff=None, semantics="forward acquisition uses retrieval-time cutoff"),
    }


def festival_job_facets() -> dict:
    return {
        "parserVersion": _facet(NAMESPACE, "festival/version/1-0-0", value="ticketmaster_event-v1"),
        "normalizationVersion": _facet(NAMESPACE, "festival/version/1-0-0", value="ticketmaster_discovery_v2-v1"),
        "softwareVersion": _facet(NAMESPACE, "festival/version/1-0-0", value="live_data_activation_v1"),
        "identityResolutionVersion": _facet(NAMESPACE, "festival/version/1-0-0", value="ticketmaster_resolution_v1"),
        "inputFingerprint": _facet(NAMESPACE, "festival/inputFingerprint/1-0-0", value="sha256:frozen_ticketmaster_fixture_v1"),
        "providerAcquisitionRunId": _facet(NAMESPACE, "festival/acquisitionRun/1-0-0", value=RUN_ID),
    }


def main() -> None:
    out = Path(tempfile.mkdtemp(prefix="openlineage_")) / "events.json"
    client = OpenLineageClient(
        transport=FileTransport(FileConfig(log_file_path=str(out), append=True))
    )

    run = Run(runId=RUN_ID, facets=festival_run_facets())
    job = Job(namespace=NAMESPACE, name=JOB_NAME, facets=festival_job_facets())

    inputs = [Dataset(namespace="festival-bloomberg", name="frozen_ticketmaster_fixture")]
    outputs = [
        Dataset(namespace="festival-bloomberg", name="events.provider_event_snapshots"),
        Dataset(namespace="festival-bloomberg", name="audit.provider_acquisition_runs"),
    ]

    client.emit(RunEvent(eventType=RunState.START, eventTime="2026-08-19T12:00:00Z",
                         run=run, job=job, producer=NAMESPACE, inputs=inputs, outputs=[]))
    client.emit(RunEvent(eventType=RunState.COMPLETE, eventTime="2026-08-19T12:00:02Z",
                         run=run, job=job, producer=NAMESPACE, inputs=inputs, outputs=outputs))

    lines = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    print(f"OpenLineage events emitted to: {out}")
    print(f"event count = {len(lines)} (expect 2: START + COMPLETE)")
    for ev in lines:
        print(f"  {ev['eventType']}: job={ev['job']['name']} run={ev['run']['runId']} "
              f"outputs={[o['name'] for o in ev.get('outputs', [])]}")
    # Confirm Festival facets survived serialization.
    facets = lines[-1]["run"]["facets"]
    print(f"  festival facets on run: {sorted(k for k in facets if k != '')}")
    assert len(lines) == 2
    assert lines[0]["eventType"] == "START" and lines[1]["eventType"] == "COMPLETE"
    assert "sourcePolicyStatus" in lines[1]["run"]["facets"]
    assert "providerAcquisitionRunId" in lines[1]["job"]["facets"]
    print("\nOPENLINEAGE_FILE_PROOF = PASS")


if __name__ == "__main__":
    main()
