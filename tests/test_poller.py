from datetime import timedelta
from pathlib import Path

import pytest

from golf_etl.config import Settings
from golf_etl.drive.client import Folders
from golf_etl.drive.poller import Poller
from golf_etl.pipeline import SessionResult
from tests.fake_drive import FakeDrive


class FakeProcess:
    """Writes a one-swing session named after the checksum; can be told to fail."""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail_on: set[str] = set()

    def __call__(
        self, video: Path, out_root: Path, *, source_name, checksum, uploaded_at, duplicates
    ):
        self.calls.append({"name": source_name, "checksum": checksum, "duplicates": duplicates})
        if source_name in self.fail_on:
            raise RuntimeError(f"cannot decode {source_name}")
        sid = f"2026-10-04-1200-{checksum[:8]}"
        swing = out_root / sid / "swing-01"
        swing.mkdir(parents=True)
        (swing / "01-address.jpg").write_bytes(b"jpg" + video.read_bytes())
        (swing / "clip.mp4").write_bytes(b"mp4")
        (out_root / sid / "session.md").write_text(f"# Session {sid}\n")
        return SessionResult(sid, out_root / sid, 1, [])


@pytest.fixture
def env(tmp_path):
    drive = FakeDrive()
    folders = Folders.resolve(drive, "golf")
    process = FakeProcess()

    def run():
        return Poller(drive, folders, Settings(), process, tmp_path / "scratch", "test").run()

    return drive, folders, process, run


def sessions(drive, folders):
    return drive.names_in(folders.sessions)


def test_resolve_creates_the_folder_layout_once():
    drive = FakeDrive()
    first = Folders.resolve(drive, "golf")
    assert Folders.resolve(drive, "golf") == first
    assert drive.tree() == ["golf", "golf/failed", "golf/inbox", "golf/processing", "golf/sessions"]


def test_video_becomes_a_session_and_the_original_is_deleted(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"swing one")
    report = run()
    assert len(report.published) == 1
    sid = report.published[0]
    assert sessions(drive, folders) == [sid]
    assert drive.names_in(folders.inbox) == []
    assert drive.names_in(folders.processing) == []
    assert "IMG_1.MOV" in drive.deleted
    assert f"golf/sessions/{sid}/swing-01/01-address.jpg" in drive.tree()


def test_session_folder_carries_checksum_and_files_are_tagged(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"swing one")
    run()
    session = drive.list_children(folders.sessions)[0]
    assert session.app_properties["sha256"] == process.calls[0]["checksum"]
    assert session.app_properties["sourceName"] == "IMG_1.MOV"
    clip = next(f for f in drive.list_tagged() if f.name == "clip.mp4")
    assert clip.app_properties["kind"] == "clip"
    assert clip.app_properties["sessionFolder"] == session.id


def test_non_video_files_are_left_alone(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox)
    drive.add_video("strike.jpg", folders.inbox, b"photo", mime="image/jpeg")
    run()
    assert drive.names_in(folders.inbox) == ["strike.jpg"]


def test_duplicates_in_one_poll_are_processed_once(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"same", created=drive.now)
    drive.add_video(
        "IMG_1 (1).MOV", folders.inbox, b"same", created=drive.now + timedelta(seconds=5)
    )
    report = run()
    assert len(process.calls) == 1
    assert process.calls[0]["duplicates"] == ["IMG_1 (1).MOV"]
    assert report.duplicates == ["IMG_1 (1).MOV"]
    assert len(sessions(drive, folders)) == 1
    assert drive.names_in(folders.inbox) == []


def test_reupload_after_success_replaces_the_session(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"same")
    run()
    first = drive.list_children(folders.sessions)[0]
    drive.add_video("IMG_1.MOV", folders.inbox, b"same")
    run()
    now = drive.list_children(folders.sessions)
    assert [s.name for s in now] == [first.name]
    assert now[0].id != first.id
    assert len(process.calls) == 2


def test_same_filename_different_content_makes_two_sessions(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"monday")
    drive.add_video("IMG_1.MOV", folders.inbox, b"friday")
    run()
    assert len(sessions(drive, folders)) == 2


def test_first_and_second_failures_go_back_to_the_inbox(env):
    drive, folders, process, run = env
    fid = drive.add_video("bad.MOV", folders.inbox)
    process.fail_on.add("bad.MOV")
    assert run().retrying == ["bad.MOV"]
    assert drive.files[fid].app_properties["attempts"] == "1"
    assert drive.names_in(folders.inbox) == ["bad.MOV"]
    run()
    assert drive.files[fid].app_properties["attempts"] == "2"


def test_third_failure_moves_to_failed_with_an_error_file(env):
    drive, folders, process, run = env
    drive.add_video("bad.MOV", folders.inbox)
    process.fail_on.add("bad.MOV")
    run()
    run()
    assert run().failed == ["bad.MOV"]
    assert drive.names_in(folders.failed) == ["bad.MOV", "bad.MOV.error.txt"]
    error = next(f for f in drive.list_children(folders.failed) if f.name.endswith(".txt"))
    assert b"cannot decode bad.MOV" in drive.content[error.id]
    assert drive.names_in(folders.inbox) == []


def test_video_left_in_processing_is_recovered_and_retried(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.processing, props={"attempts": "1"})
    report = run()
    assert report.recovered == ["IMG_1.MOV"]
    assert len(report.published) == 1
    assert drive.names_in(folders.processing) == []


def test_recovery_counts_as_an_attempt(env):
    drive, folders, process, run = env
    fid = drive.add_video("IMG_1.MOV", folders.processing)
    process.fail_on.add("IMG_1.MOV")
    run()
    assert drive.files[fid].app_properties["attempts"] == "2"  # recovery, then the failure


def test_recovered_video_past_the_limit_goes_to_failed(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.processing, props={"attempts": "2"})
    assert run().failed == ["IMG_1.MOV"]


def test_reupload_of_a_failed_video_clears_it_and_starts_fresh(env):
    drive, folders, process, run = env
    drive.add_video("bad.MOV", folders.inbox, b"data")
    process.fail_on.add("bad.MOV")
    run()
    run()
    run()
    process.fail_on.clear()
    fresh = drive.add_video("bad.MOV", folders.inbox, b"data")
    report = run()
    assert drive.names_in(folders.failed) == []
    assert len(report.published) == 1
    assert fresh not in drive.files


def test_crash_after_upload_keeps_the_previous_session(env, monkeypatch):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"same")
    run()
    old = drive.list_children(folders.sessions)[0]
    drive.add_video("IMG_1.MOV", folders.inbox, b"same")
    real_rename = drive.rename

    def die_on_swap(file_id, name):
        if name.startswith(".old-"):
            raise RuntimeError("killed")
        real_rename(file_id, name)

    monkeypatch.setattr(drive, "rename", die_on_swap)
    run()
    assert old.id in drive.files
    assert drive.files[old.id].name == old.name
    assert f"golf/sessions/{old.name}/swing-01/01-address.jpg" in drive.tree()


def test_leftover_temp_folder_is_replaced_on_the_next_publish(env):
    drive, folders, process, run = env
    drive.add_video("IMG_1.MOV", folders.inbox, b"same")
    run()
    sid = sessions(drive, folders)[0]
    drive.create_folder(f".tmp-{sid}", folders.sessions)
    drive.add_video("IMG_1.MOV", folders.inbox, b"same")
    run()
    assert sessions(drive, folders) == [sid]


def test_one_bad_video_does_not_stop_the_others(env):
    drive, folders, process, run = env
    drive.add_video("bad.MOV", folders.inbox, b"bad")
    drive.add_video("good.MOV", folders.inbox, b"good")
    process.fail_on.add("bad.MOV")
    report = run()
    assert report.retrying == ["bad.MOV"]
    assert len(report.published) == 1


def test_awkward_filenames_survive_the_round_trip(env):
    drive, folders, process, run = env
    name = "Bob's swing #2 (café).MOV"
    drive.add_video(name, folders.inbox, b"swing")
    process.fail_on.add(name)
    run()
    run()
    run()
    assert drive.names_in(folders.failed) == sorted([name, f"{name}.error.txt"])
