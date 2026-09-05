"""Enforce the single Factor CURRENT publication contract at S3 request dispatch."""
from __future__ import annotations

FACTOR_CURRENT = "gold/artist_factor_tape/CURRENT.json"


def enforce_factor_publication(params, model, **kwargs):
    operation = model.name
    keys = [params.get("Key")]
    if operation == "DeleteObjects":
        keys.extend(o.get("Key") for o in params.get("Delete", {}).get("Objects", []))
    if FACTOR_CURRENT not in keys:
        return
    if operation in {"GetObject", "HeadObject"}:
        return
    if operation == "PutObject" and (
        (isinstance(params.get("IfMatch"), str) and params["IfMatch"].strip() not in {"", "*"})
        or params.get("IfNoneMatch") == "*"
    ):
        return
    raise ValueError("FACTOR_CURRENT_REQUIRES_CONDITIONAL_PUT")


def guard_s3_client(client):
    client.meta.events.register("before-parameter-build.s3", enforce_factor_publication)
    return client
