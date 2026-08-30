"""Shared R2 client for lake tooling (loads rclone.conf credentials like the bulk uploader)."""

from __future__ import annotations

import configparser
import os
from pathlib import Path

R2_ENDPOINT = "https://51b88c6a6ef833b3c2ff46e98d5d9356.r2.cloudflarestorage.com"


def load_r2_credentials() -> tuple[str, str]:
    ak = os.environ.get("R2_ACCESS_KEY_ID", "")
    sk = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    if ak and sk:
        return ak, sk
    rclone_conf = Path.home() / ".config" / "rclone" / "rclone.conf"
    if rclone_conf.exists():
        cfg = configparser.ConfigParser()
        cfg.read(str(rclone_conf))
        if "r2" in cfg:
            ak = cfg["r2"].get("access_key_id", "")
            sk = cfg["r2"].get("secret_access_key", "")
            if ak and sk:
                os.environ["R2_ACCESS_KEY_ID"] = ak
                os.environ["R2_SECRET_ACCESS_KEY"] = sk
                return ak, sk
    return "", ""


def r2_client():
    import boto3
    from botocore.config import Config

    ak, sk = load_r2_credentials()
    if not ak or not sk:
        raise RuntimeError("R2 credentials not found (env or ~/.config/rclone/rclone.conf)")
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
        config=Config(max_pool_connections=8, retries={"max_attempts": 3, "mode": "adaptive"}),
        region_name="auto",
    )
