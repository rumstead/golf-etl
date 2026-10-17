from datetime import timedelta

import pytest

from golf_etl.config import Settings
from golf_etl.storage.retention import sweep
from tests.fake_store import FakeStore

DAY = timedelta(days=1)


@pytest.fixture
def store():
    return FakeStore()


def add_session(store, name: str, age: timedelta, clip_bytes=10, frame_bytes=5) -> None:
    when = store.now - age
    store.add(f"sessions/{name}/.golf-etl.json", b'{"sha256": "x"}', when)
    store.add(f"sessions/{name}/swing-01/clip.mp4", b"x" * clip_bytes, when)
    store.add(f"sessions/{name}/swing-01/01-address.jpg", b"x" * frame_bytes, when)


def test_young_sessions_are_untouched(store):
    add_session(store, "s1", 2 * DAY)
    before = store.tree()
    assert sweep(store, Settings(), store.now).deleted == []
    assert store.tree() == before


def test_clips_expire_before_frames(store):
    add_session(store, "s1", 15 * DAY)
    assert sweep(store, Settings(), store.now).deleted == ["clip.mp4"]
    assert "sessions/s1/swing-01/01-address.jpg" in store.tree()


def test_old_sessions_are_deleted_whole(store):
    add_session(store, "old", 61 * DAY)
    add_session(store, "new", 1 * DAY)
    sweep(store, Settings(), store.now)
    assert store.names_in("sessions") == ["new"]


def test_failed_originals_expire_three_days_after_they_failed(store):
    store.add("failed/old.MOV", modified=store.now - 400 * DAY)  # shot long ago
    store.add("failed/old.MOV.error.txt", modified=store.now - 4 * DAY)
    store.add("failed/recent.MOV", modified=store.now - 400 * DAY)
    store.add("failed/recent.MOV.error.txt", modified=store.now - 2 * DAY)
    sweep(store, Settings(), store.now)
    assert store.names_in("failed") == ["recent.MOV", "recent.MOV.error.txt"]


def test_leftover_temp_folders_are_cleaned_after_a_day(store):
    add_session(store, ".tmp-s1", 2 * DAY)
    add_session(store, ".tmp-s3", timedelta(hours=1))
    sweep(store, Settings(), store.now)
    assert store.names_in("sessions") == [".tmp-s3"]


def test_an_orphaned_old_session_is_restored(store):
    add_session(store, ".old-s1", 2 * DAY)
    report = sweep(store, Settings(), store.now)
    assert store.names_in("sessions") == ["s1"]
    assert report.restored == ["s1"]
    assert "sessions/s1/swing-01/01-address.jpg" in store.tree()


def test_an_old_session_is_deleted_once_its_replacement_is_in_place(store):
    add_session(store, ".old-s1", 2 * DAY)
    add_session(store, "s1", timedelta(hours=1))
    sweep(store, Settings(), store.now)
    assert store.names_in("sessions") == ["s1"]


def test_size_cap_deletes_oldest_sessions_first(store):
    for i, age in enumerate((5, 4, 3, 2)):
        add_session(store, f"s{i}", age * DAY, clip_bytes=40, frame_bytes=10)
    report = sweep(store, Settings(max_bytes=150), store.now)
    assert store.names_in("sessions") == ["s2", "s3"]
    assert report.bytes_after == 130  # two sessions of 40 + 10 + 15 marker bytes


def test_failed_originals_do_not_evict_sessions(store):
    add_session(store, "s1", 2 * DAY, clip_bytes=40, frame_bytes=10)
    store.add("failed/huge.MOV", b"x" * 1000)
    store.add("failed/huge.MOV.error.txt", b"err")
    report = sweep(store, Settings(max_bytes=120), store.now)
    assert store.names_in("sessions") == ["s1"]
    assert report.bytes_after == 65
