import json
import os
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import pytest

from golf_etl.config import Settings
from golf_etl.upload import (
    finish_upload,
    hook_response,
    make_server,
    status,
    sweep_uploads,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
TTL = timedelta(hours=24)


def add_upload(upload_dir, uid, data, size, filename="IMG_4601.MOV", age=timedelta(hours=1)):
    """A tusd filestore upload: the data file <id> and its <id>.info."""
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / uid
    path.write_bytes(data)
    info = {
        "ID": uid,
        "Size": size,
        "Offset": len(data),
        "MetaData": {"filename": filename, "filetype": "video/quicktime"},
        "Storage": {
            "Type": "filestore",
            "Path": str(path),
            "InfoPath": str(upload_dir / f"{uid}.info"),
        },
    }
    (upload_dir / f"{uid}.info").write_text(json.dumps(info))
    mtime = (NOW - age).timestamp()
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def dirs(tmp_path):
    return tmp_path / "uploads", tmp_path / "inbox"


def test_finished_upload_moves_into_the_inbox_under_its_name(dirs):
    uploads, inbox = dirs
    upload = add_upload(uploads, "abc", b"video", 5)
    assert finish_upload(upload, "IMG_4601.MOV", inbox) == inbox / "IMG_4601.MOV"
    assert (inbox / "IMG_4601.MOV").read_bytes() == b"video"
    assert not upload.exists()
    assert not (uploads / "abc.info").exists()


def test_finished_upload_with_a_taken_name_gets_a_suffix(dirs):
    uploads, inbox = dirs
    inbox.mkdir()
    (inbox / "IMG_4601.MOV").write_bytes(b"first")
    second = add_upload(uploads, "b", b"second", 6)
    assert finish_upload(second, "IMG_4601.MOV", inbox) == inbox / "IMG_4601-2.MOV"
    third = add_upload(uploads, "c", b"third", 5)
    assert finish_upload(third, "IMG_4601.MOV", inbox) == inbox / "IMG_4601-3.MOV"
    assert (inbox / "IMG_4601.MOV").read_bytes() == b"first"
    assert (inbox / "IMG_4601-2.MOV").read_bytes() == b"second"


def test_finished_upload_name_cannot_leave_the_inbox(dirs):
    uploads, inbox = dirs
    for uid, name, expected in [
        ("a", "../../etc/x.mov", "x.mov"),
        ("b", "", "upload.mov"),
        ("c", ".hidden.mov", "hidden.mov"),
        ("d", "..\\x\x00\x1f.mov", "x-2.mov"),
    ]:
        upload = add_upload(uploads, uid, b"v", 1)
        assert finish_upload(upload, name, inbox) == inbox / expected
    names = sorted(p.name for p in inbox.iterdir())
    assert names == ["hidden.mov", "upload.mov", "x-2.mov", "x.mov"]


def test_finishing_an_upload_twice_is_quiet(dirs):
    uploads, inbox = dirs
    upload = add_upload(uploads, "abc", b"video", 5)
    finish_upload(upload, "IMG_4601.MOV", inbox)
    assert finish_upload(upload, "IMG_4601.MOV", inbox) is None
    assert [p.name for p in inbox.iterdir()] == ["IMG_4601.MOV"]


def test_sweep_moves_a_complete_upload_whose_hook_was_missed(dirs):
    uploads, inbox = dirs
    add_upload(uploads, "abc", b"video", 5)
    assert sweep_uploads(uploads, inbox, NOW, TTL) == ["IMG_4601.MOV"]
    assert (inbox / "IMG_4601.MOV").read_bytes() == b"video"
    assert list(uploads.iterdir()) == []


def test_sweep_deletes_an_unfinished_upload_after_the_ttl(dirs):
    uploads, inbox = dirs
    add_upload(uploads, "abc", b"vid", 5, age=timedelta(hours=25))
    assert sweep_uploads(uploads, inbox, NOW, TTL) == ["IMG_4601.MOV"]
    assert list(uploads.iterdir()) == []
    assert not inbox.exists()


def test_sweep_keeps_a_young_unfinished_upload(dirs):
    uploads, inbox = dirs
    add_upload(uploads, "abc", b"vid", 5, age=timedelta(hours=1))
    assert sweep_uploads(uploads, inbox, NOW, TTL) == []
    assert sorted(p.name for p in uploads.iterdir()) == ["abc", "abc.info"]


def test_sweep_keeps_an_old_upload_that_is_still_locked(dirs):
    uploads, inbox = dirs
    add_upload(uploads, "abc", b"vid", 5, age=timedelta(hours=25))
    (uploads / "abc.lock").write_text("")
    assert sweep_uploads(uploads, inbox, NOW, TTL) == []
    assert sorted(p.name for p in uploads.iterdir()) == ["abc", "abc.info", "abc.lock"]


def test_status_lists_uploading_waiting_and_recent(tmp_path):
    uploads = tmp_path / "uploads"
    add_upload(uploads, "abc", b"vid", 10, filename="IMG_2.MOV")
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "IMG_1.MOV").write_bytes(b"video")
    (tmp_path / "inbox" / "strike.jpg").write_bytes(b"jpg")
    (tmp_path / "processing").mkdir()
    (tmp_path / "processing" / "IMG_0.MOV").write_bytes(b"claimed")
    recent = {
        "name": "IMG_0.MOV",
        "sha256": "f" * 64,
        "status": "processing",
        "updated": NOW.isoformat(),
    }
    (tmp_path / ".golf-etl").mkdir()
    (tmp_path / ".golf-etl" / "state.json").write_text(
        json.dumps({"attempts": {}, "recent": [recent]})
    )
    assert status(tmp_path, uploads) == {
        "uploading": [{"name": "IMG_2.MOV", "offset": 3, "size": 10}],
        "waiting": [{"name": "IMG_1.MOV", "size": 5}],
        "recent": [recent],
    }


def test_status_of_an_empty_volume(tmp_path):
    assert status(tmp_path, tmp_path / "uploads") == {"uploading": [], "waiting": [], "recent": []}


def test_pre_create_hook_rejects_non_videos():
    payload = {"Type": "pre-create", "Event": {"Upload": {"MetaData": {"filename": "a.zip"}}}}
    assert hook_response(payload, Settings()) == {
        "RejectUpload": True,
        "HTTPResponse": {"StatusCode": 400, "Body": "only videos"},
    }


def test_pre_create_hook_accepts_videos():
    payload = {"Type": "pre-create", "Event": {"Upload": {"MetaData": {"filename": "IMG_1.MOV"}}}}
    assert hook_response(payload, Settings()) == {}


def post_finish(path, filename="IMG_4601.MOV"):
    return {
        "Type": "post-finish",
        "Event": {
            "Upload": {
                "ID": path.name,
                "Size": 5,
                "Offset": 5,
                "MetaData": {"filename": filename, "filetype": "video/quicktime"},
                "Storage": {"Type": "filestore", "Path": str(path), "InfoPath": f"{path}.info"},
            },
            "HTTPRequest": {"Method": "PATCH", "URI": f"/files/{path.name}"},
        },
    }


def test_post_finish_hook_moves_the_upload(tmp_path):
    cfg = Settings(inbox_root=str(tmp_path), upload_dir=str(tmp_path / "uploads"))
    upload = add_upload(tmp_path / "uploads", "abc", b"video", 5)
    assert hook_response(post_finish(upload), cfg) == {}
    assert (tmp_path / "inbox" / "IMG_4601.MOV").read_bytes() == b"video"
    assert not upload.exists()


def test_post_finish_hook_ignores_files_outside_the_upload_dir(tmp_path):
    cfg = Settings(inbox_root=str(tmp_path), upload_dir=str(tmp_path / "uploads"))
    other = add_upload(tmp_path / "elsewhere", "abc", b"video", 5)
    assert hook_response(post_finish(other), cfg) == {}
    assert other.exists()
    assert not (tmp_path / "inbox").exists()


def test_server_serves_the_page_status_and_hooks(tmp_path):
    cfg = Settings(inbox_root=str(tmp_path), upload_dir=str(tmp_path / "uploads"), upload_port=0)
    server = make_server(cfg)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/") as resp:
            assert resp.headers["Content-Type"].startswith("text/html")
            assert "tus.Upload" in resp.read().decode()
        with urllib.request.urlopen(f"{base}/status") as resp:
            assert set(json.load(resp)) == {"uploading", "waiting", "recent"}
        hook = urllib.request.Request(
            f"{base}/hooks",
            data=json.dumps({"Type": "post-create", "Event": {"Upload": {}}}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(hook) as resp:
            assert json.load(resp) == {}
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"{base}/nope")
        assert exc.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
