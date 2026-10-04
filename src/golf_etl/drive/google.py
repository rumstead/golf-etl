"""Drive v3 implementation of the Drive protocol."""

import mimetypes
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from golf_etl.drive.client import FOLDER_MIME, TAG, DriveFile

SCOPES = ["https://www.googleapis.com/auth/drive"]
FIELDS = "id,name,mimeType,parents,createdTime,size,sha256Checksum,md5Checksum,appProperties"
CHUNK = 32 * 1024 * 1024
RETRIES = 3


def credentials(client_id: str, client_secret: str, refresh_token: str) -> Credentials:
    return Credentials(
        None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
        token_uri="https://oauth2.googleapis.com/token",
    )


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _to_file(f: dict) -> DriveFile:
    return DriveFile(
        id=f["id"],
        name=f["name"],
        mime_type=f["mimeType"],
        parents=tuple(f.get("parents", [])),
        created=datetime.fromisoformat(f["createdTime"].replace("Z", "+00:00")),
        size=int(f.get("size", 0)),
        sha256=f.get("sha256Checksum"),
        md5=f.get("md5Checksum"),
        app_properties=f.get("appProperties", {}),
    )


class GoogleDrive:
    def __init__(self, service):
        self.files = service.files()

    @classmethod
    def connect(cls, creds: Credentials) -> "GoogleDrive":
        return cls(build("drive", "v3", credentials=creds, cache_discovery=False))

    def _list(self, q: str) -> list[DriveFile]:
        out: list[DriveFile] = []
        token = None
        while True:
            resp = self.files.list(
                q=f"{q} and trashed = false",
                spaces="drive",
                pageSize=1000,
                fields=f"nextPageToken,files({FIELDS})",
                pageToken=token,
            ).execute(num_retries=RETRIES)
            out += [_to_file(f) for f in resp.get("files", [])]
            token = resp.get("nextPageToken")
            if not token:
                return out

    def find_folder(self, name: str, parent_id: str) -> str | None:
        found = self._list(
            f"{_quote(parent_id)} in parents and name = {_quote(name)} "
            f"and mimeType = {_quote(FOLDER_MIME)}"
        )
        return found[0].id if found else None

    def create_folder(
        self, name: str, parent_id: str, props: Mapping[str, str] | None = None
    ) -> str:
        body = {
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
            "appProperties": dict(props or {}),
        }
        return self.files.create(body=body, fields="id").execute(num_retries=RETRIES)["id"]

    def list_children(self, folder_id: str) -> list[DriveFile]:
        return self._list(f"{_quote(folder_id)} in parents")

    def list_tagged(self) -> list[DriveFile]:
        return self._list(f"appProperties has {{ key={_quote(TAG)} and value='1' }}")

    def find_by_property(self, parent_id: str, key: str, value: str) -> list[DriveFile]:
        return self._list(
            f"{_quote(parent_id)} in parents and "
            f"appProperties has {{ key={_quote(key)} and value={_quote(value)} }}"
        )

    def move(self, file_id: str, new_parent_id: str) -> None:
        current = self.files.get(fileId=file_id, fields="parents").execute(num_retries=RETRIES)
        self.files.update(
            fileId=file_id,
            addParents=new_parent_id,
            removeParents=",".join(current.get("parents", [])),
            fields="id",
        ).execute(num_retries=RETRIES)

    def rename(self, file_id: str, name: str) -> None:
        self.files.update(fileId=file_id, body={"name": name}, fields="id").execute(
            num_retries=RETRIES
        )

    def set_properties(self, file_id: str, props: Mapping[str, str]) -> None:
        self.files.update(fileId=file_id, body={"appProperties": dict(props)}, fields="id").execute(
            num_retries=RETRIES
        )

    def download(self, file_id: str, dest: Path) -> None:
        with dest.open("wb") as fh:
            downloader = MediaIoBaseDownload(
                fh, self.files.get_media(fileId=file_id), chunksize=CHUNK
            )
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=RETRIES)

    def upload(self, src: Path, parent_id: str, props: Mapping[str, str]) -> str:
        mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
        media = MediaFileUpload(str(src), mimetype=mime, resumable=True, chunksize=CHUNK)
        body = {"name": src.name, "parents": [parent_id], "appProperties": dict(props)}
        return self.files.create(body=body, media_body=media, fields="id").execute(
            num_retries=RETRIES
        )["id"]

    def delete(self, file_id: str) -> None:
        self.files.delete(fileId=file_id).execute(num_retries=RETRIES)
