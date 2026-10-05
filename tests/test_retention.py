from datetime import timedelta

import pytest

from golf_etl.config import Settings
from golf_etl.drive.retention import sweep
from tests.fake_drive import FakeDrive

DAY = timedelta(days=1)


@pytest.fixture
def drive():
    return FakeDrive()


def add_session(drive, name: str, age: timedelta, clip_bytes=10, frame_bytes=5) -> None:
    when = drive.now - age
    drive.add(f"sessions/{name}/.golf-etl.json", b'{"sha256": "x"}', when)
    drive.add(f"sessions/{name}/swing-01/clip.mp4", b"x" * clip_bytes, when)
    drive.add(f"sessions/{name}/swing-01/01-address.jpg", b"x" * frame_bytes, when)


def test_young_sessions_are_untouched(drive):
    add_session(drive, "s1", 2 * DAY)
    before = drive.tree()
    assert sweep(drive, Settings(), drive.now).deleted == []
    assert drive.tree() == before


def test_clips_expire_before_frames(drive):
    add_session(drive, "s1", 15 * DAY)
    assert sweep(drive, Settings(), drive.now).deleted == ["clip.mp4"]
    assert "sessions/s1/swing-01/01-address.jpg" in drive.tree()


def test_old_sessions_are_deleted_whole(drive):
    add_session(drive, "old", 61 * DAY)
    add_session(drive, "new", 1 * DAY)
    sweep(drive, Settings(), drive.now)
    assert drive.names_in("sessions") == ["new"]


def test_failed_originals_expire_three_days_after_they_failed(drive):
    drive.add("failed/old.MOV", modified=drive.now - 400 * DAY)  # shot long ago
    drive.add("failed/old.MOV.error.txt", modified=drive.now - 4 * DAY)
    drive.add("failed/recent.MOV", modified=drive.now - 400 * DAY)
    drive.add("failed/recent.MOV.error.txt", modified=drive.now - 2 * DAY)
    sweep(drive, Settings(), drive.now)
    assert drive.names_in("failed") == ["recent.MOV", "recent.MOV.error.txt"]


def test_leftover_temp_folders_are_cleaned_after_a_day(drive):
    add_session(drive, ".tmp-s1", 2 * DAY)
    add_session(drive, ".tmp-s3", timedelta(hours=1))
    sweep(drive, Settings(), drive.now)
    assert drive.names_in("sessions") == [".tmp-s3"]


def test_an_orphaned_old_session_is_restored(drive):
    add_session(drive, ".old-s1", 2 * DAY)
    report = sweep(drive, Settings(), drive.now)
    assert drive.names_in("sessions") == ["s1"]
    assert report.restored == ["s1"]
    assert "sessions/s1/swing-01/01-address.jpg" in drive.tree()


def test_an_old_session_is_deleted_once_its_replacement_is_in_place(drive):
    add_session(drive, ".old-s1", 2 * DAY)
    add_session(drive, "s1", timedelta(hours=1))
    sweep(drive, Settings(), drive.now)
    assert drive.names_in("sessions") == ["s1"]


def test_size_cap_deletes_oldest_sessions_first(drive):
    for i, age in enumerate((5, 4, 3, 2)):
        add_session(drive, f"s{i}", age * DAY, clip_bytes=40, frame_bytes=10)
    report = sweep(drive, Settings(max_bytes=150), drive.now)
    assert drive.names_in("sessions") == ["s2", "s3"]
    assert report.bytes_after == 130  # two sessions of 40 + 10 + 15 marker bytes


def test_failed_originals_do_not_evict_sessions(drive):
    add_session(drive, "s1", 2 * DAY, clip_bytes=40, frame_bytes=10)
    drive.add("failed/huge.MOV", b"x" * 1000)
    drive.add("failed/huge.MOV.error.txt", b"err")
    report = sweep(drive, Settings(max_bytes=120), drive.now)
    assert drive.names_in("sessions") == ["s1"]
    assert report.bytes_after == 65
