"""Structured run logger with secret redaction for economics collector.

Logs operational metadata only. Never logs API keys, auth headers, or secrets.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..acquisition.contracts import utc_now

# Exit codes
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_LOCK_HELD = 2
EXIT_AUTH_FAILURE = 3
EXIT_NO_ACTIVE_EVENTS = 4

# Provider status constants
PROVIDER_AUTH_VALID = "AUTH_VALID"
PROVIDER_NOT_CONFIGURED = "NOT_CONFIGURED"
PROVIDER_AUTH_FAILED = "AUTH_FAILED"
PROVIDER_RATE_LIMITED = "RATE_LIMITED"
PROVIDER_ERROR = "ERROR"

# Software version
SOFTWARE_VERSION = "forward_market_history_v1"


class RunLogger:
    """Structured logger for collector runs with secret redaction."""
    
    def __init__(self, log_dir: str | Path | None = None, max_log_size_mb: int = 5) -> None:
        if log_dir is None:
            # Use XDG state directory or fallback
            xdg_state = os.environ.get("XDG_STATE_HOME")
            if xdg_state:
                log_dir = Path(xdg_state) / "festival-bloomberg"
            else:
                log_dir = Path.home() / ".local" / "state" / "festival-bloomberg"
        
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.max_log_size_bytes = max_log_size_mb * 1024 * 1024
        self.run_id = str(uuid4())
        self.started_at = utc_now()
        self.finished_at: datetime | None = None
        self.events_attempted = 0
        self.events_succeeded = 0
        self.provider_status: dict[str, str] = {}
        self.snapshots_appended = 0
        self.snapshots_deduped = 0
        self.errors: list[str] = []
        self.cost_usd = 0.0
        self.quota_metadata: dict[str, Any] = {}
        self.exit_code: int | None = None
    
    def log_provider_status(self, provider: str, status: str) -> None:
        """Log provider status as safe enum only."""
        self.provider_status[provider] = status
    
    def log_error(self, error: str) -> None:
        """Log error message (will be redacted if contains secrets)."""
        self.errors.append(self._redact_secrets(error))
    
    def increment_events_attempted(self, count: int = 1) -> None:
        self.events_attempted += count
    
    def increment_events_succeeded(self, count: int = 1) -> None:
        self.events_succeeded += count
    
    def increment_snapshots_appended(self, count: int = 1) -> None:
        self.snapshots_appended += count
    
    def increment_snapshots_deduped(self, count: int = 1) -> None:
        self.snapshots_deduped += count
    
    def add_cost(self, cost_usd: float) -> None:
        self.cost_usd += cost_usd
    
    def set_quota_metadata(self, metadata: dict[str, Any]) -> None:
        """Set quota metadata (will be redacted)."""
        self.quota_metadata = self._redact_dict_secrets(metadata)
    
    def finish(self, exit_code: int) -> None:
        """Mark run as finished and write log."""
        self.finished_at = utc_now()
        self.exit_code = exit_code
        self._write_log()
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "events_attempted": self.events_attempted,
            "events_succeeded": self.events_succeeded,
            "provider_status": self.provider_status,
            "snapshots_appended": self.snapshots_appended,
            "snapshots_deduped": self.snapshots_deduped,
            "errors": self.errors,
            "cost_usd": self.cost_usd,
            "quota_metadata": self.quota_metadata,
            "exit_code": self.exit_code,
            "software_version": SOFTWARE_VERSION,
        }
    
    def _write_log(self) -> None:
        """Write structured log to file with rotation."""
        log_file = self.log_dir / "economics_collector.log"
        
        # Rotate if too large
        if log_file.exists() and log_file.stat().st_size > self.max_log_size_bytes:
            archived = log_file.with_suffix(".log.1")
            if archived.exists():
                archived.unlink()
            log_file.rename(archived)
        
        # Append new log entry
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(self.to_dict()) + "\n")
    
    def _redact_secrets(self, text: str) -> str:
        """Redact potential secrets from text."""
        if not text:
            return text
        
        # Redact common secret patterns
        redacted = text
        
        # API keys (common patterns)
        redacted = redacted.replace(os.environ.get("TICKETMASTER_API_KEY", ""), "[REDACTED]")
        redacted = redacted.replace(os.environ.get("SEATGEEK_CLIENT_ID", ""), "[REDACTED]")
        redacted = redacted.replace(os.environ.get("SEATGEEK_CLIENT_SECRET", ""), "[REDACTED]")
        
        # Authorization headers
        if "Authorization:" in redacted or "authorization:" in redacted:
            redacted = "[REDACTED_AUTH_HEADER]"
        
        # API key patterns
        if "api_key=" in redacted.lower() or "apikey=" in redacted.lower():
            redacted = "[REDACTED_API_KEY]"
        
        # Bearer tokens
        if "Bearer " in redacted:
            redacted = redacted.replace("Bearer ", "Bearer [REDACTED]")
        
        return redacted
    
    def _redact_dict_secrets(self, data: dict[str, Any]) -> dict[str, Any]:
        """Recursively redact secrets from dictionary."""
        if not isinstance(data, dict):
            return data
        
        redacted = {}
        for key, value in data.items():
            # Skip known secret keys entirely
            if any(secret in key.lower() for secret in ["key", "secret", "token", "password", "auth"]):
                redacted[key] = "[REDACTED]"
            elif isinstance(value, str):
                redacted[key] = self._redact_secrets(value)
            elif isinstance(value, dict):
                redacted[key] = self._redact_dict_secrets(value)
            elif isinstance(value, list):
                redacted[key] = [self._redact_dict_secrets(item) if isinstance(item, dict) else item for item in value]
            else:
                redacted[key] = value
        
        return redacted


def persist_run_to_db(economics_repo, run_logger: RunLogger) -> None:
    """Persist run log to economics.collector_runs table."""
    row = {
        "run_id": run_logger.run_id,
        "started_at": run_logger.started_at.isoformat(),
        "finished_at": run_logger.finished_at.isoformat() if run_logger.finished_at else None,
        "events_attempted": run_logger.events_attempted,
        "events_succeeded": run_logger.events_succeeded,
        "provider_status_json": json.dumps(run_logger.provider_status),
        "snapshots_appended": run_logger.snapshots_appended,
        "snapshots_deduped": run_logger.snapshots_deduped,
        "errors_json": json.dumps(run_logger.errors),
        "cost_usd": run_logger.cost_usd,
        "quota_metadata_json": json.dumps(run_logger.quota_metadata),
        "exit_code": run_logger.exit_code,
        "software_version": SOFTWARE_VERSION,
        "knowledge_time": utc_now().isoformat(),
    }
    
    economics_repo.conn.execute(
        """
        INSERT INTO economics.collector_runs
            (run_id, started_at, finished_at, events_attempted, events_succeeded,
             provider_status_json, snapshots_appended, snapshots_deduped, errors_json,
             cost_usd, quota_metadata_json, exit_code, software_version, knowledge_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            row["run_id"],
            row["started_at"],
            row["finished_at"],
            row["events_attempted"],
            row["events_succeeded"],
            row["provider_status_json"],
            row["snapshots_appended"],
            row["snapshots_deduped"],
            row["errors_json"],
            row["cost_usd"],
            row["quota_metadata_json"],
            row["exit_code"],
            row["software_version"],
            row["knowledge_time"],
        ],
    )
    economics_repo.conn.commit()
