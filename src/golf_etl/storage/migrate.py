"""One-time move of the Drive sessions to the GitHub repo, each deleted from Drive once verified."""

import fnmatch
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from golf_etl.storage.github import EXCLUDE, GitHubPublisher
from golf_etl.storage.store import FAILED, INBOX, MARKER, PROCESSING, SESSIONS, Store

log = logging.getLogger(__name__)

SKIP = (*EXCLUDE, "session.zip")  # fnmatch on file names


@dataclass
class MigrateReport:
    """Session ids by outcome, and in blocked the inbox, processing and failed entries.

    A dry run lists in copied and deleted what a run would do if every push verifies.
    """

    copied: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)


def migrate(
    store: Store, publisher: GitHubPublisher, scratch: Path, dry_run: bool
) -> MigrateReport:
    """Copy every Drive session to the repo, then delete the ones the remote has in full.

    Refuses while anything is in inbox/, processing/ or failed/, so the owner decides first.
    """
    report = MigrateReport()
    for folder in (INBOX, PROCESSING, FAILED):
        report.blocked += [e.path for e in store.list(folder)]
    if report.blocked:
        return report
    files: dict[str, list[str]] = {}
    for e in store.list(SESSIONS, recursive=True):
        sid, _, rel = e.path.removeprefix(f"{SESSIONS}/").partition("/")
        if sid.startswith("."):
            continue
        if not rel:
            if e.is_dir:
                files.setdefault(sid, [])
            continue
        if not e.is_dir and not any(fnmatch.fnmatch(e.name, p) for p in SKIP):
            files.setdefault(sid, []).append(rel)
    for sid, rels in sorted(files.items()):
        if MARKER not in rels:
            log.warning("%s has no %s, keeping it on Drive", sid, MARKER)
            report.kept.append(sid)
            continue
        log.info("%s: %d files", sid, len(rels))
        if dry_run:
            report.copied.append(sid)
            report.deleted.append(sid)
            continue
        local = scratch / sid
        shutil.rmtree(local, ignore_errors=True)
        try:
            for rel in rels:
                dest = local / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                store.download(f"{SESSIONS}/{sid}/{rel}", dest)
            marker = json.loads((local / MARKER).read_text())
            publisher.publish(local, sid, marker)
            report.copied.append(sid)
            if _verified(publisher, sid, local, rels, marker):
                store.delete(f"{SESSIONS}/{sid}")
                report.deleted.append(sid)
            else:
                report.kept.append(sid)
        finally:
            shutil.rmtree(local, ignore_errors=True)
    return report


def _verified(
    publisher: GitHubPublisher, sid: str, local: Path, rels: list[str], marker: dict
) -> bool:
    """Every downloaded file is on the remote with the same size.

    The publisher writes the marker itself, so its bytes can differ: it must parse to the same
    JSON. published_files leaves the remote's copy of the session checked out in the work dir.
    """
    published = publisher.published_files(sid)
    for rel in rels:
        if rel not in published:
            log.warning("%s: %s is missing from the push", sid, rel)
            return False
        if rel == MARKER:
            pushed = publisher.work / SESSIONS / sid / MARKER
            if json.loads(pushed.read_text()) != marker:
                log.warning("%s: the pushed %s differs", sid, MARKER)
                return False
        elif published[rel] != (local / rel).stat().st_size:
            log.warning("%s: %s has a different size in the push", sid, rel)
            return False
    return True
