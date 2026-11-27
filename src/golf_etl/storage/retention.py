"""Delete failed originals once they are old enough."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from golf_etl.config import Settings
from golf_etl.storage.store import FAILED, Store


@dataclass
class SweepReport:
    deleted: list[str] = field(default_factory=list)


def sweep_failed(store: Store, cfg: Settings, now: datetime) -> SweepReport:
    report = SweepReport()
    # Failed originals age from their error file: a video's own time is when it was shot.
    failed = store.list(FAILED)
    errors = {e.name.removesuffix(".error.txt"): e for e in failed if e.name.endswith(".error.txt")}
    for e in failed:
        stamp = errors.get(e.name.removesuffix(".error.txt"), e).modified
        if now - stamp > timedelta(days=cfg.failed_ttl_days):
            store.delete(e.path)
            report.deleted.append(e.name)
    return report
