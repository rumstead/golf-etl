"""In-memory Drive for poller and retention tests, addressed by path like RcloneDrive.

Like real Drive it allows two files with the same name in a folder; any operation on such an
ambiguous path fails until dedupe() renames them.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from golf_etl.drive.client import Entry


@dataclass
class FakeFile:
    path: str
    data: bytes
    modified: datetime


def _parent(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "" if parent == "." else parent


def _under(path: str, folder: str) -> bool:
    return folder == "" or path == folder or path.startswith(folder + "/")


class FakeDrive:
    def __init__(self, now: datetime | None = None):
        self.now = now or datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        self.files: list[FakeFile] = []
        self.dirs: dict[str, datetime] = {"": self.now}
        self.deleted: list[str] = []  # names, in delete order

    # helpers for tests
    def add(self, path: str, data: bytes = b"video", modified: datetime | None = None) -> str:
        self.mkdir(_parent(path))
        self.files.append(FakeFile(path, data, modified or self.now))
        return path

    def add_video(
        self,
        name: str,
        folder: str = "inbox",
        data: bytes = b"video",
        modified: datetime | None = None,
    ) -> str:
        return self.add(f"{folder}/{name}", data, modified)

    def names_in(self, folder: str) -> list[str]:
        return sorted(PurePosixPath(e.path).name for e in self.list(folder))

    def tree(self) -> list[str]:
        return sorted(f.path for f in self.files)

    def content(self, path: str) -> bytes:
        return self._file(path).data

    def _file(self, path: str) -> FakeFile:
        matches = [f for f in self.files if f.path == path]
        if len(matches) != 1:
            raise RuntimeError(f"{len(matches)} files at {path}")
        return matches[0]

    def _is_dir(self, path: str) -> bool:
        return path in self.dirs

    # Drive protocol
    def list(self, folder, recursive=False, hashes=False):
        if not self._is_dir(folder):
            return []
        out = []
        for d, when in self.dirs.items():
            if d and d != folder and _under(d, folder) and (recursive or _parent(d) == folder):
                out.append(Entry(d, 0, when, is_dir=True))
        for f in self.files:
            if _under(f.path, folder) and (recursive or _parent(f.path) == folder):
                mime = "video/quicktime" if f.path.lower().endswith(".mov") else ""
                sha = hashlib.sha256(f.data).hexdigest() if hashes else None
                out.append(Entry(f.path, len(f.data), f.modified, mime=mime, sha256=sha))
        return sorted(out, key=lambda e: e.path)

    def mkdir(self, folder):
        parts = PurePosixPath(folder).parts if folder else ()
        for i in range(1, len(parts) + 1):
            self.dirs.setdefault("/".join(parts[:i]), self.now)

    def move(self, src, dst):
        if self._is_dir(src):
            for d in [d for d in self.dirs if _under(d, src)]:
                self.dirs[dst + d[len(src) :]] = self.dirs.pop(d)
            for f in self.files:
                if _under(f.path, src):
                    f.path = dst + f.path[len(src) :]
            self.mkdir(_parent(dst))
            return
        f = self._file(src)
        self.mkdir(_parent(dst))
        self.files = [g for g in self.files if g.path != dst or g is f]
        f.path = dst

    def download(self, src, dest: Path):
        dest.write_bytes(self._file(src).data)

    def upload_tree(self, src: Path, dst):
        self.mkdir(dst)
        for p in sorted(src.rglob("*")):
            rel = f"{dst}/{p.relative_to(src).as_posix()}"
            if p.is_dir():
                self.mkdir(rel)
            else:
                self.add(rel, p.read_bytes(), self.now)

    def read_text(self, path):
        matches = [f for f in self.files if f.path == path]
        return matches[0].data.decode() if matches else None

    def write_text(self, path, text):
        self.files = [f for f in self.files if f.path != path]
        self.add(path, text.encode(), self.now)

    def delete(self, path):
        if self._is_dir(path):
            self.deleted.append(PurePosixPath(path).name)
            self.files = [f for f in self.files if not _under(f.path, path)]
            for d in [d for d in self.dirs if _under(d, path)]:
                del self.dirs[d]
            return
        f = self._file(path)
        self.deleted.append(PurePosixPath(path).name)
        self.files.remove(f)

    def dedupe(self, folder):
        by_path: dict[str, list[FakeFile]] = {}
        for f in self.files:
            if _parent(f.path) == folder:
                by_path.setdefault(f.path, []).append(f)
        for path, group in by_path.items():
            if len(group) > 1:
                p = PurePosixPath(path)
                for i, f in enumerate(group, 1):
                    f.path = str(p.with_name(f"{p.stem}-{i}{p.suffix}"))
