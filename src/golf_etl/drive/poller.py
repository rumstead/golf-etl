"""One poll of the Drive inbox: recover, dedupe, claim, process, publish, clean up."""

import json
import logging
import shutil
import traceback
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from golf_etl.config import Settings
from golf_etl.drive.client import (
    FAILED,
    INBOX,
    MARKER,
    PROCESSING,
    SESSIONS,
    Drive,
    Entry,
    State,
    ensure_layout,
)
from golf_etl.pipeline import SessionResult

log = logging.getLogger(__name__)

# process(video, out_root, *, source_name, checksum, uploaded_at, duplicates) -> SessionResult
Processor = Callable[..., SessionResult]


@dataclass
class PollReport:
    recovered: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    published: list[str] = field(default_factory=list)
    retrying: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def session_checksum(drive: Drive, folder: str) -> str | None:
    text = drive.read_text(f"{folder}/{MARKER}")
    return json.loads(text).get("sha256") if text else None


@dataclass
class Poller:
    drive: Drive
    cfg: Settings
    process: Processor
    scratch: Path
    version: str
    report: PollReport = field(default_factory=PollReport)
    state: State = field(default_factory=State)

    def run(self) -> PollReport:
        ensure_layout(self.drive)
        self.state = State.load(self.drive)
        self.recover()
        self.drive.dedupe(INBOX)
        groups: dict[str, list[Entry]] = defaultdict(list)
        for e in self.drive.list(INBOX, hashes=True):
            if e.is_video and e.sha256:
                groups[e.sha256].append(e)
        for checksum, copies in groups.items():
            copies.sort(key=lambda e: (e.modified, e.path))
            keep, extras = copies[0], copies[1:]
            for extra in extras:
                self.drive.delete(extra.path)
                self.report.duplicates.append(extra.name)
            self.clear_failed(checksum)
            self.handle(keep, checksum, [e.name for e in extras])
        return self.report

    def recover(self) -> None:
        """Anything in processing/ at startup was left by a run that died."""
        for e in self.drive.list(PROCESSING, hashes=True):
            if e.is_dir:
                continue
            self.report.recovered.append(e.name)
            self.attempt_failed(e.path, e.name, e.sha256 or e.name, "the run ended mid-video")

    def clear_failed(self, checksum: str) -> None:
        """A re-upload of a video that landed in failed/ replaces it and starts fresh."""
        failed = self.drive.list(FAILED, hashes=True)
        for e in failed:
            if e.sha256 != checksum:
                continue
            self.drive.delete(e.path)
            error = f"{FAILED}/{e.name}.error.txt"
            if any(f.path == error for f in failed):
                self.drive.delete(error)
            if self.state.attempts.pop(checksum, None) is not None:
                self.state.save(self.drive)

    def handle(self, video: Entry, checksum: str, duplicates: list[str]) -> None:
        claimed = f"{PROCESSING}/{video.name}"
        self.drive.move(video.path, claimed)
        log.info("processing %s (%s)", video.name, checksum[:8])
        work = self.scratch / checksum[:16]
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            local = work / video.name
            self.drive.download(claimed, local)
            result = self.process(
                local,
                work / "out",
                source_name=video.name,
                checksum=checksum,
                uploaded_at=video.modified,
                duplicates=duplicates,
            )
            self.publish(result, checksum, video.name)
            self.drive.delete(claimed)
            if self.state.attempts.pop(checksum, None) is not None:
                self.state.save(self.drive)
            self.report.published.append(result.session_id)
            log.info("published %s with %d swings", result.session_id, result.swings)
        except Exception:
            log.exception("processing %s failed", video.name)
            self.attempt_failed(claimed, video.name, checksum, traceback.format_exc())
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def attempt_failed(self, path: str, name: str, checksum: str, error: str) -> None:
        attempts = self.state.attempts.get(checksum, 0) + 1
        self.state.attempts[checksum] = attempts
        self.state.save(self.drive)
        if attempts < self.cfg.max_attempts:
            self.drive.move(path, f"{INBOX}/{name}")
            self.report.retrying.append(name)
            log.warning("%s will be retried (attempt %d)", name, attempts)
            return
        self.drive.move(path, f"{FAILED}/{name}")
        self.drive.write_text(
            f"{FAILED}/{name}.error.txt",
            f"{name} failed {attempts} times. Last error:\n\n{error}\n",
        )
        self.state.attempts.pop(checksum, None)
        self.state.save(self.drive)
        self.report.failed.append(name)
        log.error("%s moved to failed/ after %d attempts", name, attempts)

    def publish(self, result: SessionResult, checksum: str, source_name: str) -> None:
        """Upload to .tmp-<id>, then swap it in. A crash at any step leaves a readable session."""
        sid = result.session_id
        tmp, live, old = f"{SESSIONS}/.tmp-{sid}", f"{SESSIONS}/{sid}", f"{SESSIONS}/.old-{sid}"
        names = {e.name for e in self.drive.list(SESSIONS) if e.is_dir}
        if f".tmp-{sid}" in names:
            self.drive.delete(tmp)
        self.drive.upload_tree(result.session_dir, tmp)
        marker = {
            "sha256": checksum,
            "sourceName": source_name,
            "pipelineVersion": self.version,
            "published": datetime.now(UTC).isoformat(),
        }
        self.drive.write_text(f"{tmp}/{MARKER}", json.dumps(marker, indent=2))
        if sid in names:
            # A leftover .old-<id> next to a live session is from a swap that finished.
            if f".old-{sid}" in names:
                self.drive.delete(old)
            self.drive.move(live, old)
        self.drive.move(tmp, live)
        if sid in names or f".old-{sid}" in names:
            self.drive.delete(old)
