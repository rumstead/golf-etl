"""The slice of Google Drive the poller needs. GoogleDrive implements it; tests use a fake."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

FOLDER_MIME = "application/vnd.google-apps.folder"
TAG = "golfEtl"  # appProperties key set on everything the pipeline creates


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    parents: tuple[str, ...]
    created: datetime
    size: int = 0
    sha256: str | None = None
    md5: str | None = None
    app_properties: Mapping[str, str] = field(default_factory=dict)

    @property
    def checksum(self) -> str | None:
        return self.sha256 or self.md5

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

    @property
    def is_video(self) -> bool:
        return self.mime_type.startswith("video/")


class Drive(Protocol):
    def find_folder(self, name: str, parent_id: str) -> str | None: ...
    def create_folder(
        self, name: str, parent_id: str, props: Mapping[str, str] | None = None
    ) -> str: ...
    def list_children(self, folder_id: str) -> list[DriveFile]: ...
    def list_tagged(self) -> list[DriveFile]: ...
    def find_by_property(self, parent_id: str, key: str, value: str) -> list[DriveFile]: ...
    def move(self, file_id: str, new_parent_id: str) -> None: ...
    def rename(self, file_id: str, name: str) -> None: ...
    def set_properties(self, file_id: str, props: Mapping[str, str]) -> None: ...
    def download(self, file_id: str, dest: Path) -> None: ...
    def upload(self, src: Path, parent_id: str, props: Mapping[str, str]) -> str: ...
    def delete(self, file_id: str) -> None:
        """Permanent delete, skipping trash. Folders take their contents with them."""
        ...


ROOT = "root"


def ensure_folder(drive: Drive, name: str, parent_id: str) -> str:
    return drive.find_folder(name, parent_id) or drive.create_folder(name, parent_id)


@dataclass(frozen=True)
class Folders:
    root: str
    inbox: str
    processing: str
    failed: str
    sessions: str

    @classmethod
    def resolve(cls, drive: Drive, root_name: str) -> "Folders":
        root = ensure_folder(drive, root_name, ROOT)
        return cls(
            root,
            *(ensure_folder(drive, n, root) for n in ("inbox", "processing", "failed", "sessions")),
        )
