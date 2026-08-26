"""Regression: festival_bloomberg.config and festival_bloomberg.cloud must coexist.

The cloud-data-plane-v1 migration originally created a config/ package that
shadowed the existing config.py module, breaking credential imports.
This test prevents that from happening again.
"""

from __future__ import annotations


def test_config_module_not_shadowed():
    """Verify the existing credential-status API is still importable."""
    from festival_bloomberg.config import all_credential_status, credential_status

    assert callable(all_credential_status)
    assert callable(credential_status)


def test_cloud_package_importable():
    """Verify the new cloud package is importable."""
    from festival_bloomberg.cloud.r2_storage import R2Config, get_config

    assert callable(get_config)

    # R2Config.from_env should default to local mode (no env set in test)
    import os

    saved = os.environ.pop("FI_OBJECT_STORE", None)
    try:
        cfg = R2Config.from_env()
        assert cfg.enabled is False
        assert cfg.raw_bucket == "festival-intelligence-raw"
    finally:
        if saved is not None:
            os.environ["FI_OBJECT_STORE"] = saved


def test_both_namespaces_coexist():
    """Both must be importable in the same process."""
    from festival_bloomberg.config import all_credential_status
    from festival_bloomberg.cloud.r2_storage import R2Config

    assert all_credential_status is not None
    assert R2Config is not None
