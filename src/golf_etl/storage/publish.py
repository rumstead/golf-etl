"""Where a finished session goes. The poller hands it over and the publisher owns the rest."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from golf_etl.storage.retention import SweepReport


class Publisher(Protocol):
    def publish(self, session_dir: Path, session_id: str, marker: dict) -> None: ...

    def sweep(self, now: datetime) -> SweepReport:
        """Apply the retention rules to published sessions."""
        ...


def marker(checksum: str, source_name: str, version: str, venue: str) -> dict:
    return {
        "sha256": checksum,
        "sourceName": source_name,
        "pipelineVersion": version,
        "published": datetime.now(UTC).isoformat(),
        "venue": venue,
    }
