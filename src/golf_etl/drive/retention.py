"""Keep golf/ inside its Drive budget: TTLs first, then a size cap on whole sessions.

The cap counts session files only. Failed originals have their own short TTL, so one large
failed upload cannot push every session out.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from golf_etl.config import Settings
from golf_etl.drive.client import FAILED, MARKER, SESSIONS, Drive

LEFTOVER_TTL = timedelta(days=1)


@dataclass
class SweepReport:
    deleted: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    bytes_after: int = 0


def sweep(drive: Drive, cfg: Settings, now: datetime) -> SweepReport:
    report = SweepReport()

    def delete(path: str) -> None:
        drive.delete(path)
        report.deleted.append(PurePosixPath(path).name)

    # Failed originals age from their error file: a video's own time is when it was shot.
    failed = drive.list(FAILED)
    errors = {e.name.removesuffix(".error.txt"): e for e in failed if e.name.endswith(".error.txt")}
    for e in failed:
        stamp = errors.get(e.name.removesuffix(".error.txt"), e).modified
        if now - stamp > timedelta(days=cfg.failed_ttl_days):
            delete(e.path)

    entries = drive.list(SESSIONS, recursive=True)
    top = [e for e in entries if e.is_dir and "/" not in e.path[len(SESSIONS) + 1 :]]
    names = {e.name for e in top}
    published = {PurePosixPath(e.path).parent.name: e.modified for e in entries if e.name == MARKER}
    gone: set[str] = set()
    for s in top:
        if s.name.startswith(".old-"):
            # Left by a run that died mid-swap: drop it if the new session made it into place,
            # otherwise it is still the only copy, so put it back.
            sid = s.name.removeprefix(".old-")
            if sid in names:
                delete(s.path)
                gone.add(s.name)
            else:
                drive.move(s.path, f"{SESSIONS}/{sid}")
                names.add(sid)
                report.restored.append(sid)
                gone.add(s.name)
            continue
        age = now - published.get(s.name, s.modified)
        leftover = s.name.startswith(".tmp-") and age > LEFTOVER_TTL
        if leftover or age > timedelta(days=cfg.session_ttl_days):
            delete(s.path)
            gone.add(s.name)

    def session_of(path: str) -> str:
        return path[len(SESSIONS) + 1 :].split("/", 1)[0]

    sizes: dict[str, int] = defaultdict(int)
    for e in entries:
        if e.is_dir or session_of(e.path) in gone:
            continue
        if e.name == "clip.mp4" and now - e.modified > timedelta(days=cfg.clip_ttl_days):
            delete(e.path)
            continue
        sizes[session_of(e.path)] += e.size

    total = sum(sizes.values())
    live = sorted(
        (s for s in top if s.name not in gone and not s.name.startswith(".")),
        key=lambda s: published.get(s.name, s.modified),
    )
    for s in live:
        if total <= cfg.max_bytes:
            break
        delete(s.path)
        total -= sizes.pop(s.name, 0)
    report.bytes_after = total
    return report
