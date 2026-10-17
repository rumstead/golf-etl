"""One poll of the Drive inbox: recover, dedupe, claim, process, publish, clean up."""

import logging
import shutil
import traceback
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from golf_etl.config import Settings
from golf_etl.pipeline import SessionResult
from golf_etl.storage.publish import Publisher, marker
from golf_etl.storage.store import (
    FAILED,
    INBOX,
    PROCESSING,
    Entry,
    State,
    Store,
    ensure_layout,
)

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


@dataclass
class Poller:
    inbox: Store
    publisher: Publisher
    cfg: Settings
    process: Processor
    scratch: Path
    version: str
    report: PollReport = field(default_factory=PollReport)
    state: State = field(default_factory=State)

    def run(self) -> PollReport:
        ensure_layout(self.inbox)
        self.state = State.load(self.inbox)
        self.recover()
        self.inbox.dedupe(INBOX)
        groups: dict[str, list[Entry]] = defaultdict(list)
        for e in self.inbox.list(INBOX, hashes=True):
            if e.is_video and e.sha256:
                groups[e.sha256].append(e)
        for checksum, copies in groups.items():
            copies.sort(key=lambda e: (e.modified, e.path))
            keep, extras = copies[0], copies[1:]
            for extra in extras:
                self.inbox.delete(extra.path)
                self.report.duplicates.append(extra.name)
            self.clear_failed(checksum)
            self.handle(keep, checksum, [e.name for e in extras])
        return self.report

    def recover(self) -> None:
        """Anything in processing/ at startup was left by a run that died."""
        for e in self.inbox.list(PROCESSING, hashes=True):
            if e.is_dir:
                continue
            self.report.recovered.append(e.name)
            self.attempt_failed(e.path, e.name, e.sha256 or e.name, "the run ended mid-video")

    def clear_failed(self, checksum: str) -> None:
        """A re-upload of a video that landed in failed/ replaces it and starts fresh."""
        failed = self.inbox.list(FAILED, hashes=True)
        for e in failed:
            if e.sha256 != checksum:
                continue
            self.inbox.delete(e.path)
            error = f"{FAILED}/{e.name}.error.txt"
            if any(f.path == error for f in failed):
                self.inbox.delete(error)
            if self.state.attempts.pop(checksum, None) is not None:
                self.state.save(self.inbox)

    def handle(self, video: Entry, checksum: str, duplicates: list[str]) -> None:
        claimed = f"{PROCESSING}/{video.name}"
        self.inbox.move(video.path, claimed)
        self.state.note(video.name, checksum, "processing", datetime.now(UTC))
        self.state.save(self.inbox)
        log.info("processing %s (%s)", video.name, checksum[:8])
        work = self.scratch / checksum[:16]
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            local = work / video.name
            self.inbox.download(claimed, local)
            result = self.process(
                local,
                work / "out",
                source_name=video.name,
                checksum=checksum,
                uploaded_at=video.modified,
                duplicates=duplicates,
            )
            self.publisher.publish(
                result.session_dir, result.session_id, marker(checksum, video.name, self.version)
            )
            self.inbox.delete(claimed)
            self.state.attempts.pop(checksum, None)
            self.state.note(
                video.name, checksum, "published", datetime.now(UTC), session=result.session_id
            )
            self.state.save(self.inbox)
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
        last_line = next((ln for ln in reversed(error.splitlines()) if ln.strip()), "")
        retrying = attempts < self.cfg.max_attempts
        self.state.note(
            name,
            checksum,
            "retrying" if retrying else "failed",
            datetime.now(UTC),
            error=last_line.strip(),
        )
        self.state.save(self.inbox)
        if retrying:
            self.inbox.move(path, f"{INBOX}/{name}")
            self.report.retrying.append(name)
            log.warning("%s will be retried (attempt %d)", name, attempts)
            return
        self.inbox.move(path, f"{FAILED}/{name}")
        self.inbox.write_text(
            f"{FAILED}/{name}.error.txt",
            f"{name} failed {attempts} times. Last error:\n\n{error}\n",
        )
        self.state.attempts.pop(checksum, None)
        self.state.save(self.inbox)
        self.report.failed.append(name)
        log.error("%s moved to failed/ after %d attempts", name, attempts)
