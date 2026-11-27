"""Store on a local folder: the inbox root on the data volume."""

import hashlib
import mimetypes
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from golf_etl.storage.store import Entry


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalStore:
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, rel: str) -> Path:
        return self.root / rel if rel else self.root

    def _entry(self, path: Path, hashes: bool) -> Entry:
        stat = path.stat()
        is_dir = path.is_dir()
        return Entry(
            path=path.relative_to(self.root).as_posix(),
            size=0 if is_dir else stat.st_size,
            modified=datetime.fromtimestamp(stat.st_mtime, UTC),
            is_dir=is_dir,
            mime="" if is_dir else mimetypes.guess_type(path.name)[0] or "",
            sha256=_sha256(path) if hashes and not is_dir else None,
        )

    def list(self, folder: str, recursive: bool = False, hashes: bool = False) -> list[Entry]:
        base = self._path(folder)
        if not base.is_dir():
            return []
        paths = base.rglob("*") if recursive else base.iterdir()
        entries = []
        for path in paths:
            try:
                entries.append(self._entry(path, hashes))
            except FileNotFoundError:  # removed while listing
                continue
        return sorted(entries, key=lambda e: e.path)

    def mkdir(self, folder: str) -> None:
        self._path(folder).mkdir(parents=True, exist_ok=True)

    def move(self, src: str, dst: str) -> None:
        target = self._path(dst)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(self._path(src), target)

    def download(self, src: str, dest: Path) -> None:
        shutil.copyfile(self._path(src), dest)

    def read_text(self, path: str) -> str | None:
        try:
            return self._path(path).read_text()
        except FileNotFoundError:
            return None

    def write_text(self, path: str, text: str) -> None:
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_text(text)
        os.replace(tmp, target)

    def delete(self, path: str) -> None:
        target = self._path(path)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
