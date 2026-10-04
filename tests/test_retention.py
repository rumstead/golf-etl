from datetime import timedelta
from pathlib import Path

import pytest

from golf_etl.config import Settings
from golf_etl.drive.client import TAG, Folders
from golf_etl.drive.retention import sweep
from tests.fake_drive import FakeDrive

DAY = timedelta(days=1)


@pytest.fixture
def drive():
    return FakeDrive()


@pytest.fixture
def folders(drive):
    return Folders.resolve(drive, "golf")


def add_session(
    drive, folders, tmp_path: Path, name: str, age: timedelta, clip_bytes=10, frame_bytes=5
) -> str:
    created = drive.now - age
    drive.now, real_now = created, drive.now
    sid = drive.create_folder(name, folders.sessions, {TAG: "1", "kind": "session", "sha256": name})
    swing = drive.create_folder("swing-01", sid, {TAG: "1", "kind": "swing", "sessionFolder": sid})
    for fname, size, kind in (
        ("clip.mp4", clip_bytes, "clip"),
        ("01-address.jpg", frame_bytes, "file"),
    ):
        src = tmp_path / fname
        src.write_bytes(b"x" * size)
        drive.upload(src, swing, {TAG: "1", "kind": kind, "sessionFolder": sid})
    drive.now = real_now
    return sid


def test_young_sessions_are_untouched(drive, folders, tmp_path):
    add_session(drive, folders, tmp_path, "s1", 2 * DAY)
    before = drive.tree()
    assert sweep(drive, folders, Settings(), drive.now).deleted == []
    assert drive.tree() == before


def test_clips_expire_before_frames(drive, folders, tmp_path):
    add_session(drive, folders, tmp_path, "s1", 15 * DAY)
    report = sweep(drive, folders, Settings(), drive.now)
    assert report.deleted == ["clip.mp4"]
    assert "golf/sessions/s1/swing-01/01-address.jpg" in drive.tree()


def test_old_sessions_are_deleted_whole(drive, folders, tmp_path):
    add_session(drive, folders, tmp_path, "old", 61 * DAY)
    add_session(drive, folders, tmp_path, "new", 1 * DAY)
    sweep(drive, folders, Settings(), drive.now)
    assert drive.names_in(folders.sessions) == ["new"]


def test_failed_originals_expire_after_three_days(drive, folders):
    drive.add_video("old.MOV", folders.failed, created=drive.now - 4 * DAY)
    drive.add_video("recent.MOV", folders.failed, created=drive.now - 2 * DAY)
    sweep(drive, folders, Settings(), drive.now)
    assert drive.names_in(folders.failed) == ["recent.MOV"]


def test_leftover_temp_folders_are_cleaned_after_a_day(drive, folders, tmp_path):
    add_session(drive, folders, tmp_path, ".tmp-s1", 2 * DAY)
    add_session(drive, folders, tmp_path, ".tmp-s3", timedelta(hours=1))
    sweep(drive, folders, Settings(), drive.now)
    assert drive.names_in(folders.sessions) == [".tmp-s3"]


def test_an_orphaned_old_session_is_restored(drive, folders, tmp_path):
    add_session(drive, folders, tmp_path, ".old-s1", 2 * DAY)
    report = sweep(drive, folders, Settings(), drive.now)
    assert drive.names_in(folders.sessions) == ["s1"]
    assert report.restored == ["s1"]
    assert "golf/sessions/s1/swing-01/01-address.jpg" in drive.tree()


def test_an_old_session_is_deleted_once_its_replacement_is_in_place(drive, folders, tmp_path):
    add_session(drive, folders, tmp_path, ".old-s1", 2 * DAY)
    add_session(drive, folders, tmp_path, "s1", timedelta(hours=1))
    sweep(drive, folders, Settings(), drive.now)
    assert drive.names_in(folders.sessions) == ["s1"]


def test_size_cap_deletes_oldest_sessions_first(drive, folders, tmp_path):
    for i, age in enumerate((5, 4, 3, 2)):
        add_session(drive, folders, tmp_path, f"s{i}", age * DAY, clip_bytes=40, frame_bytes=10)
    cfg = Settings(max_bytes=120)
    report = sweep(drive, folders, cfg, drive.now)
    assert drive.names_in(folders.sessions) == ["s2", "s3"]
    assert report.bytes_after == 100


def test_failed_originals_do_not_evict_sessions(drive, folders, tmp_path):
    add_session(drive, folders, tmp_path, "s1", 2 * DAY, clip_bytes=40, frame_bytes=10)
    drive.add_video("huge.MOV", folders.failed, b"x" * 1000)
    report = sweep(drive, folders, Settings(max_bytes=120), drive.now)
    assert drive.names_in(folders.sessions) == ["s1"]
    assert report.bytes_after == 50
