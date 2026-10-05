"""RcloneDrive against a local folder: the real rclone binary, no Google account."""

import hashlib
import shutil

import pytest

from golf_etl.drive.rclone import RcloneDrive

pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


@pytest.fixture
def root(tmp_path):
    (tmp_path / "golf").mkdir()
    return tmp_path / "golf"


@pytest.fixture
def drive(root):
    return RcloneDrive(str(root))


def test_list_returns_files_with_sha256_and_mime(drive, root):
    (root / "inbox").mkdir()
    (root / "inbox" / "Bob's swing #2.MOV").write_bytes(b"swing")
    entries = drive.list("inbox", hashes=True)
    assert [e.path for e in entries] == ["inbox/Bob's swing #2.MOV"]
    e = entries[0]
    assert e.name == "Bob's swing #2.MOV"
    assert e.sha256 == hashlib.sha256(b"swing").hexdigest()
    assert e.is_video and not e.is_dir
    assert e.size == 5


def test_list_of_a_missing_folder_is_empty(drive):
    assert drive.list("nope") == []


def test_recursive_list_has_nested_paths(drive, root):
    (root / "sessions" / "s1" / "swing-01").mkdir(parents=True)
    (root / "sessions" / "s1" / "swing-01" / "clip.mp4").write_bytes(b"mp4")
    paths = {e.path for e in drive.list("sessions", recursive=True)}
    assert paths == {"sessions/s1", "sessions/s1/swing-01", "sessions/s1/swing-01/clip.mp4"}


def test_move_renames_files_and_folders(drive, root):
    (root / "inbox").mkdir()
    (root / "processing").mkdir()
    (root / "inbox" / "a.MOV").write_bytes(b"a")
    drive.move("inbox/a.MOV", "processing/a.MOV")
    assert (root / "processing" / "a.MOV").read_bytes() == b"a"
    (root / "sessions" / ".tmp-s1").mkdir(parents=True)
    (root / "sessions" / ".tmp-s1" / "x.jpg").write_bytes(b"x")
    drive.move("sessions/.tmp-s1", "sessions/s1")
    assert (root / "sessions" / "s1" / "x.jpg").exists()
    assert not (root / "sessions" / ".tmp-s1").exists()


def test_upload_tree_download_and_text(drive, root, tmp_path):
    local = tmp_path / "out" / "s1"
    (local / "swing-01").mkdir(parents=True)
    (local / "swing-01" / "01-address.jpg").write_bytes(b"jpg")
    (local / "session.md").write_text("# Session s1\n")
    drive.upload_tree(local, "sessions/s1")
    assert (root / "sessions" / "s1" / "swing-01" / "01-address.jpg").read_bytes() == b"jpg"
    drive.write_text("sessions/s1/.golf-etl.json", '{"sha256": "abc"}')
    assert drive.read_text("sessions/s1/.golf-etl.json") == '{"sha256": "abc"}'
    assert drive.read_text("sessions/s1/missing.json") is None
    dest = tmp_path / "down.md"
    drive.download("sessions/s1/session.md", dest)
    assert dest.read_text() == "# Session s1\n"


def test_delete_removes_files_and_whole_folders(drive, root):
    (root / "failed").mkdir()
    (root / "failed" / "bad.MOV").write_bytes(b"x")
    drive.delete("failed/bad.MOV")
    assert not (root / "failed" / "bad.MOV").exists()
    (root / "sessions" / "s1" / "swing-01").mkdir(parents=True)
    (root / "sessions" / "s1" / "swing-01" / "a.jpg").write_bytes(b"x")
    drive.delete("sessions/s1")
    assert not (root / "sessions" / "s1").exists()


def test_mkdir_is_idempotent(drive, root):
    drive.mkdir("inbox")
    drive.mkdir("inbox")
    assert (root / "inbox").is_dir()


def test_failures_raise_with_rclone_stderr(drive):
    with pytest.raises(RuntimeError, match="rclone moveto failed"):
        drive.move("inbox/missing.MOV", "processing/missing.MOV")


def test_missing_paths_are_detected_by_exit_code_not_message(tmp_path):
    # Drive's rclone backend can report "not found" with exit code 3 or 4 and nothing on stderr.
    stub = tmp_path / "rclone"
    stub.write_text("#!/bin/sh\nexit 4\n")
    stub.chmod(0o755)
    quiet = RcloneDrive("gdrive:golf", rclone=str(stub))
    assert quiet.read_text(".golf-etl/state.json") is None
    stub.write_text("#!/bin/sh\nexit 3\n")
    assert quiet.list("inbox") == []
