"""In-memory Drive for poller and retention tests."""

import hashlib
import itertools
import mimetypes
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from golf_etl.drive.client import FOLDER_MIME, ROOT, DriveFile


class FakeDrive:
    def __init__(self, now: datetime | None = None):
        self.now = now or datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        self.files: dict[str, DriveFile] = {}
        self.content: dict[str, bytes] = {}
        self.deleted: list[str] = []  # names, in delete order
        self._ids = itertools.count(1)

    # helpers for tests
    def add_video(
        self,
        name: str,
        parent_id: str,
        data: bytes = b"video",
        created: datetime | None = None,
        props: Mapping[str, str] | None = None,
        mime: str = "video/quicktime",
    ) -> str:
        fid = f"f{next(self._ids)}"
        self.files[fid] = DriveFile(
            fid,
            name,
            mime,
            (parent_id,),
            created or self.now,
            len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            app_properties=dict(props or {}),
        )
        self.content[fid] = data
        return fid

    def names_in(self, folder_id: str) -> list[str]:
        return sorted(f.name for f in self.list_children(folder_id))

    def path_of(self, fid: str) -> str:
        f = self.files[fid]
        parent = f.parents[0]
        return f.name if parent == ROOT else f"{self.path_of(parent)}/{f.name}"

    def tree(self) -> list[str]:
        return sorted(self.path_of(fid) for fid in self.files)

    # Drive protocol
    def find_folder(self, name, parent_id):
        return next(
            (f.id for f in self.list_children(parent_id) if f.is_folder and f.name == name), None
        )

    def create_folder(self, name, parent_id, props=None):
        fid = f"d{next(self._ids)}"
        self.files[fid] = DriveFile(
            fid, name, FOLDER_MIME, (parent_id,), self.now, app_properties=dict(props or {})
        )
        return fid

    def list_children(self, folder_id):
        return [f for f in self.files.values() if folder_id in f.parents]

    def list_tagged(self):
        return [f for f in self.files.values() if "golfEtl" in f.app_properties]

    def find_by_property(self, parent_id, key, value):
        return [f for f in self.list_children(parent_id) if f.app_properties.get(key) == value]

    def move(self, file_id, new_parent_id):
        self.files[file_id] = replace(self.files[file_id], parents=(new_parent_id,))

    def rename(self, file_id, name):
        self.files[file_id] = replace(self.files[file_id], name=name)

    def set_properties(self, file_id, props):
        f = self.files[file_id]
        self.files[file_id] = replace(f, app_properties={**f.app_properties, **props})

    def download(self, file_id, dest: Path):
        dest.write_bytes(self.content[file_id])

    def upload(self, src: Path, parent_id, props):
        fid = f"f{next(self._ids)}"
        data = src.read_bytes()
        mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
        self.files[fid] = DriveFile(
            fid,
            src.name,
            mime,
            (parent_id,),
            self.now,
            len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            app_properties=dict(props),
        )
        self.content[fid] = data
        return fid

    def delete(self, file_id):
        for child in self.list_children(file_id):
            self.delete(child.id)
        self.deleted.append(self.files[file_id].name)
        del self.files[file_id]
        self.content.pop(file_id, None)
