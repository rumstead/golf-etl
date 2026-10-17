"""Where a finished session goes. The poller hands it over and the publisher owns the rest."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from golf_etl.config import Settings
from golf_etl.storage.retention import SweepReport, sweep_sessions
from golf_etl.storage.store import MARKER, SESSIONS, Store


class Publisher(Protocol):
    def publish(self, session_dir: Path, session_id: str, marker: dict) -> None: ...

    def sweep(self, now: datetime) -> SweepReport:
        """Apply the retention rules to published sessions."""
        ...


def marker(checksum: str, source_name: str, version: str) -> dict:
    return {
        "sha256": checksum,
        "sourceName": source_name,
        "pipelineVersion": version,
        "published": datetime.now(UTC).isoformat(),
    }


class DrivePublisher:
    """Sessions in the sessions/ folder of the Drive remote."""

    def __init__(self, store: Store, cfg: Settings):
        self.store = store
        self.cfg = cfg

    def publish(self, session_dir: Path, session_id: str, marker: dict) -> None:
        """Upload to .tmp-<id>, then swap it in. A crash at any step leaves a readable session."""
        sid = session_id
        tmp, live, old = f"{SESSIONS}/.tmp-{sid}", f"{SESSIONS}/{sid}", f"{SESSIONS}/.old-{sid}"
        names = {e.name for e in self.store.list(SESSIONS) if e.is_dir}
        if f".tmp-{sid}" in names:
            self.store.delete(tmp)
        self.store.upload_tree(session_dir, tmp)
        self.store.write_text(f"{tmp}/{MARKER}", json.dumps(marker, indent=2))
        if sid in names:
            # A leftover .old-<id> next to a live session is from a swap that finished.
            if f".old-{sid}" in names:
                self.store.delete(old)
            self.store.move(live, old)
        self.store.move(tmp, live)
        if sid in names or f".old-{sid}" in names:
            self.store.delete(old)

    def sweep(self, now: datetime) -> SweepReport:
        return sweep_sessions(self.store, self.cfg, now)
