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
    restored: list[str] = field(default_factory=list)
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
    names = {s.name for s in sessions}
    for s in sessions:
        if s.name.startswith(".old-"):
            # Left by a run that died mid-swap: drop it if the new session made it into
            # place, otherwise it is still the only copy, so put it back.
            sid = s.name.removeprefix(".old-")
            if sid in names:
                delete(s)
                gone.add(s.id)
            else:
                drive.rename(s.id, sid)
                names.add(sid)
                report.restored.append(sid)
            continue
        leftover = s.name.startswith(".tmp-") and now - s.created > LEFTOVER_TTL
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
