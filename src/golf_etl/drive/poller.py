"""One poll of the Drive inbox: recover, dedupe, claim, process, publish, clean up."""

import logging
import shutil
import traceback
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from golf_etl.config import Settings
from golf_etl.drive.client import TAG, Drive, DriveFile, Folders
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


@dataclass
class Poller:
    drive: Drive
    folders: Folders
    cfg: Settings
    process: Processor
    scratch: Path
    version: str
    report: PollReport = field(default_factory=PollReport)

    def run(self) -> PollReport:
        self.recover()
        groups: dict[str, list[DriveFile]] = defaultdict(list)
        for f in self.drive.list_children(self.folders.inbox):
            if f.is_video and f.checksum:
                groups[f.checksum].append(f)
        for checksum, copies in groups.items():
            copies.sort(key=lambda f: (f.created, f.id))
            keep, extras = copies[0], copies[1:]
            for extra in extras:
                self.drive.delete(extra.id)
                self.report.duplicates.append(extra.name)
            self.clear_failed(checksum)
            self.handle(keep, checksum, [e.name for e in extras])
        return self.report

    def recover(self) -> None:
        """Anything in processing/ at startup was left by a run that died."""
        for f in self.drive.list_children(self.folders.processing):
            self.report.recovered.append(f.name)
            self.attempt_failed(f, "the run ended while processing this video")

    def clear_failed(self, checksum: str) -> None:
        for f in self.drive.list_children(self.folders.failed):
            if f.checksum == checksum or f.app_properties.get("sha256") == checksum:
                self.drive.delete(f.id)

    def handle(self, video: DriveFile, checksum: str, duplicates: list[str]) -> None:
        self.drive.move(video.id, self.folders.processing)
        log.info("processing %s (%s)", video.name, checksum[:8])
        work = self.scratch / checksum[:16]
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            local = work / video.name
            self.drive.download(video.id, local)
            result = self.process(
                local,
                work / "out",
                source_name=video.name,
                checksum=checksum,
                uploaded_at=video.created,
                duplicates=duplicates,
            )
            self.publish(result, checksum, video.name)
            self.drive.delete(video.id)
            self.report.published.append(result.session_id)
            log.info("published %s with %d swings", result.session_id, result.swings)
        except Exception:
            log.exception("processing %s failed", video.name)
            self.attempt_failed(video, traceback.format_exc())
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def attempt_failed(self, video: DriveFile, error: str) -> None:
        attempts = int(video.app_properties.get("attempts", "0")) + 1
        self.drive.set_properties(video.id, {"attempts": str(attempts)})
        if attempts < self.cfg.max_attempts:
            self.drive.move(video.id, self.folders.inbox)
            self.report.retrying.append(video.name)
            log.warning("%s will be retried (attempt %d)", video.name, attempts)
            return
        self.drive.move(video.id, self.folders.failed)
        note = self.scratch / f"{video.name}.error.txt"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"{video.name} failed {attempts} times. Last error:\n\n{error}\n")
        try:
            self.drive.upload(note, self.folders.failed, {TAG: "1", "sha256": video.checksum or ""})
        finally:
            note.unlink(missing_ok=True)
        self.report.failed.append(video.name)
        log.error("%s moved to failed/ after %d attempts", video.name, attempts)

    def publish(self, result: SessionResult, checksum: str, source_name: str) -> None:
        """Upload to .tmp-<id>, then swap it in. A crash at any step leaves a readable session."""
        sid = result.session_id
        sessions = self.folders.sessions
        for stale in self.drive.list_children(sessions):
            if stale.name in (f".tmp-{sid}", f".old-{sid}"):
                self.drive.delete(stale.id)
        tmp = self.drive.create_folder(
            f".tmp-{sid}",
            sessions,
            {
                TAG: "1",
                "kind": "session",
                "sha256": checksum,
                "sourceName": source_name,
                "pipelineVersion": self.version,
            },
        )
        self.upload_tree(result.session_dir, tmp, tmp)
        existing = [
            f
            for f in self.drive.find_by_property(sessions, "sha256", checksum)
            if f.id != tmp and not f.name.startswith(".")
        ]
        for old in existing:
            self.drive.rename(old.id, f".old-{sid}")
        self.drive.rename(tmp, sid)
        for old in existing:
            self.drive.delete(old.id)

    def upload_tree(self, local: Path, parent_id: str, session_folder: str) -> None:
        for path in sorted(local.iterdir()):
            props = {TAG: "1", "sessionFolder": session_folder}
            if path.is_dir():
                child = self.drive.create_folder(path.name, parent_id, {**props, "kind": "swing"})
                self.upload_tree(path, child, session_folder)
            else:
                kind = "clip" if path.suffix == ".mp4" else "file"
                self.drive.upload(path, parent_id, {**props, "kind": kind})
