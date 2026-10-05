"""Drive implemented with the rclone binary.

The remote comes from rclone's environment config (RCLONE_CONFIG_GDRIVE_*), so the root is
something like "gdrive:golf". Tests point it at a local folder instead.
"""

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from golf_etl.drive.client import Entry

BASE_FLAGS = ["--log-level", "ERROR", "--drive-use-trash=false"]
NOT_FOUND = {3, 4}  # rclone exit codes: directory not found, file not found


class RcloneError(RuntimeError):
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


def _parse_time(value: str) -> datetime:
    # rclone prints nanoseconds; fromisoformat takes at most microseconds
    trimmed = re.sub(r"(\.\d{6})\d+", r"\1", value).replace("Z", "+00:00")
    return datetime.fromisoformat(trimmed)


class RcloneDrive:
    def __init__(self, root: str, rclone: str = "rclone"):
        self.root = root.rstrip("/")
        self.rclone = rclone

    def _path(self, rel: str) -> str:
        return f"{self.root}/{rel}" if rel else self.root

    def _run(self, *args: str, stdin: bytes | None = None) -> bytes:
        proc = subprocess.run(
            [self.rclone, *args, *BASE_FLAGS], input=stdin, capture_output=True, check=False
        )
        if proc.returncode != 0:
            message = proc.stderr.decode(errors="replace").strip()
            raise RcloneError(f"rclone {args[0]} failed: {message}", proc.returncode)
        return proc.stdout

    def list(self, folder: str, recursive: bool = False, hashes: bool = False) -> list[Entry]:
        args = ["lsjson", self._path(folder)]
        if recursive:
            args.append("--recursive")
        if hashes:
            args += ["--hash", "--hash-type", "sha256"]
        try:
            items = json.loads(self._run(*args))
        except RcloneError as exc:
            if exc.code in NOT_FOUND or "directory not found" in str(exc):
                return []
            raise
        prefix = f"{folder}/" if folder else ""
        return [
            Entry(
                path=prefix + item["Path"],
                size=max(0, int(item.get("Size", 0))) if not item["IsDir"] else 0,
                modified=_parse_time(item["ModTime"]),
                is_dir=item["IsDir"],
                mime=item.get("MimeType", ""),
                sha256=(item.get("Hashes") or {}).get("sha256") or None,
            )
            for item in items
        ]

    def mkdir(self, folder: str) -> None:
        self._run("mkdir", self._path(folder))

    def move(self, src: str, dst: str) -> None:
        self._run("moveto", self._path(src), self._path(dst))

    def download(self, src: str, dest: Path) -> None:
        self._run("copyto", self._path(src), str(dest))

    def upload_tree(self, src: Path, dst: str) -> None:
        self._run("copy", str(src), self._path(dst))

    def read_text(self, path: str) -> str | None:
        try:
            return self._run("cat", self._path(path)).decode()
        except RcloneError as exc:
            if exc.code in NOT_FOUND or "not found" in str(exc):
                return None
            raise

    def write_text(self, path: str, text: str) -> None:
        self._run("rcat", self._path(path), stdin=text.encode())

    def delete(self, path: str) -> None:
        stat = json.loads(self._run("lsjson", "--stat", self._path(path)))
        if stat["IsDir"]:
            self._run("purge", self._path(path))
        else:
            self._run("deletefile", self._path(path))

    def dedupe(self, folder: str) -> None:
        if ":" in self.root.split("/")[0]:  # only remotes allow duplicate names
            self._run("dedupe", "--dedupe-mode", "rename", self._path(folder))
