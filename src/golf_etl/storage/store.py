"""A folder tree addressed by path: the inbox, and in drive mode the sessions too.

RcloneStore implements it on a Drive remote or a local folder; tests use an in-memory fake.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

INBOX = "inbox"
PROCESSING = "processing"
FAILED = "failed"
SESSIONS = "sessions"
STATE = ".golf-etl/state.json"
MARKER = ".golf-etl.json"  # inside each session folder
VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v"}


@dataclass(frozen=True)
class Entry:
    path: str  # relative to the golf root, "/" separated
    size: int
    modified: datetime
    is_dir: bool = False
    mime: str = ""
    sha256: str | None = None

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def is_video(self) -> bool:
        if self.is_dir:
            return False
        return self.mime.startswith("video/") or PurePosixPath(self.path).suffix.lower() in (
            VIDEO_SUFFIXES
        )


class Store(Protocol):
    def list(self, folder: str, recursive: bool = False, hashes: bool = False) -> list[Entry]:
        """Entries under folder, empty when it does not exist."""
        ...

    def mkdir(self, folder: str) -> None: ...
    def move(self, src: str, dst: str) -> None: ...
    def download(self, src: str, dest: Path) -> None: ...
    def upload_tree(self, src: Path, dst: str) -> None: ...
    def read_text(self, path: str) -> str | None: ...
    def write_text(self, path: str, text: str) -> None: ...

    def delete(self, path: str) -> None:
        """Permanent delete, skipping trash. Folders take their contents with them."""
        ...

    def dedupe(self, folder: str) -> None:
        """Rename files that share a name in folder, which Drive allows and paths cannot address."""
        ...


def ensure_layout(store: Store) -> None:
    for folder in (INBOX, PROCESSING, FAILED):
        store.mkdir(folder)


@dataclass
class State:
    """Retry counts by checksum. Runs never overlap, so one file is enough."""

    attempts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, store: Store) -> "State":
        text = store.read_text(STATE)
        return cls(dict(json.loads(text).get("attempts", {}))) if text else cls()

    def save(self, store: Store) -> None:
        store.write_text(STATE, json.dumps({"attempts": self.attempts}, indent=2, sort_keys=True))
