"""SQLite-backed operational event storage."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FitatuOperationalEvent:
    """Immutable record of a single auth lifecycle or diagnostic event."""

    event: str
    correlation_id: str
    lifecycle_state: str
    created_at: str
    payload: dict[str, Any]


class FitatuOperationalStore:
    """Small SQLite store for auth lifecycle and diagnostics."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = sqlite3.connect(str(self.path))
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        """Close the underlying SQLite connection if it is still open."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> FitatuOperationalStore:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        """Create the operational_events table if it does not exist yet."""
        assert self._connection is not None
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operational_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                lifecycle_state TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def append_event(
        self,
        *,
        event: str,
        correlation_id: str,
        lifecycle_state: str,
        payload: dict[str, Any],
    ) -> None:
        """Append a new operational event to the local SQLite store."""
        assert self._connection is not None
        created_at = datetime.now(UTC).isoformat()
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO operational_events (
                    event, correlation_id, lifecycle_state, payload, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event,
                    correlation_id,
                    lifecycle_state,
                    json.dumps(payload, ensure_ascii=True, sort_keys=True),
                    created_at,
                ),
            )

    def count_events(self) -> int:
        """Return the total number of recorded operational events."""
        assert self._connection is not None
        row = self._connection.execute(
            "SELECT COUNT(*) AS total FROM operational_events"
        ).fetchone()
        return int(row["total"]) if row else 0

    def list_recent_events(self, limit: int = 50) -> list[FitatuOperationalEvent]:
        """Return the newest stored events in reverse chronological order."""
        assert self._connection is not None
        rows = self._connection.execute(
            """
            SELECT event, correlation_id, lifecycle_state, payload, created_at
            FROM operational_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        out: list[FitatuOperationalEvent] = []
        for row in rows:
            payload_raw = row["payload"]
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else {}
            if not isinstance(payload, dict):
                payload = {}
            out.append(
                FitatuOperationalEvent(
                    event=str(row["event"]),
                    correlation_id=str(row["correlation_id"]),
                    lifecycle_state=str(row["lifecycle_state"]),
                    created_at=str(row["created_at"]),
                    payload=payload,
                )
            )
        return out
