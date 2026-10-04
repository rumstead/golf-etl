"""Keep golf/ inside its Drive budget: TTLs first, then a size cap on whole sessions.

The cap counts session files only. Failed originals have their own short TTL, so one large
failed upload cannot push every session out.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from golf_etl.config import Settings
from golf_etl.drive.client import Drive, DriveFile, Folders

LEFTOVER_TTL = timedelta(days=1)


@dataclass
class SweepReport:
    deleted: list[str] = field(default_factory=list)
    bytes_after: int = 0


def sweep(drive: Drive, folders: Folders, cfg: Settings, now: datetime) -> SweepReport:
    report = SweepReport()

    def delete(f: DriveFile) -> None:
        drive.delete(f.id)
        report.deleted.append(f.name)

    for f in drive.list_children(folders.failed):
        if now - f.created > timedelta(days=cfg.failed_ttl_days):
            delete(f)

    tagged = drive.list_tagged()
    sessions = sorted(
        (
            f
            for f in tagged
            if f.app_properties.get("kind") == "session" and folders.sessions in f.parents
        ),
        key=lambda f: f.created,
    )
    gone: set[str] = set()
    for s in sessions:
        leftover = s.name.startswith(".") and now - s.created > LEFTOVER_TTL
        if leftover or now - s.created > timedelta(days=cfg.session_ttl_days):
            delete(s)
            gone.add(s.id)
    sessions = [s for s in sessions if s.id not in gone]

    for f in tagged:
        if (
            f.app_properties.get("kind") == "clip"
            and f.app_properties.get("sessionFolder") not in gone
            and now - f.created > timedelta(days=cfg.clip_ttl_days)
        ):
            delete(f)
            gone.add(f.id)

    by_session: dict[str, int] = defaultdict(int)
    for f in tagged:
        if f.id not in gone and f.app_properties.get("sessionFolder") not in gone:
            by_session[f.app_properties.get("sessionFolder", "")] += f.size
    total = sum(by_session.values())
    for s in sessions:
        if total <= cfg.max_bytes:
            break
        if s.name.startswith("."):
            continue
        delete(s)
        total -= by_session.pop(s.id, 0)
    report.bytes_after = total
    return report
