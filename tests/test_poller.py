import json
from datetime import timedelta
from pathlib import Path

import pytest

from golf_etl.config import Settings
from golf_etl.drive.client import STATE, State
from golf_etl.drive.poller import Poller
from golf_etl.drive.retention import sweep
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
    process = FakeProcess()

    def run():
        return Poller(drive, Settings(), process, tmp_path / "scratch", "test").run()

    return drive, process, run


def sessions(drive):
    return drive.names_in("sessions")


def attempts(drive, name_data: bytes) -> int:
    import hashlib

    return State.load(drive).attempts.get(hashlib.sha256(name_data).hexdigest(), 0)


def test_first_run_creates_the_folder_layout(env):
    drive, process, run = env
    run()
    assert {"inbox", "processing", "failed", "sessions"} <= set(drive.dirs)


def test_video_becomes_a_session_and_the_original_is_deleted(env):
    drive, process, run = env
    drive.add_video("IMG_1.MOV", data=b"swing one")
    report = run()
    assert len(report.published) == 1
    sid = report.published[0]
    assert sessions(drive) == [sid]
    assert drive.names_in("inbox") == []
    assert drive.names_in("processing") == []
    assert "IMG_1.MOV" in drive.deleted
    assert f"sessions/{sid}/swing-01/01-address.jpg" in drive.tree()


def test_session_folder_has_a_marker_with_the_checksum(env):
    drive, process, run = env
    drive.add_video("IMG_1.MOV", data=b"swing one")
    sid = run().published[0]
    marker = json.loads(drive.read_text(f"sessions/{sid}/.golf-etl.json"))
    assert marker["sha256"] == process.calls[0]["checksum"]
    assert marker["sourceName"] == "IMG_1.MOV"
    assert marker["pipelineVersion"] == "test"


def test_non_video_files_are_left_alone(env):
    drive, process, run = env
    drive.add_video("IMG_1.MOV")
    drive.add("inbox/strike.jpg", b"photo")
    run()
    assert drive.names_in("inbox") == ["strike.jpg"]


def test_duplicates_in_one_poll_are_processed_once(env):
    drive, process, run = env
    drive.add_video("IMG_1.MOV", data=b"same", modified=drive.now)
    drive.add_video("IMG_1 (1).MOV", data=b"same", modified=drive.now + timedelta(seconds=5))
    report = run()
    assert len(process.calls) == 1
    assert process.calls[0]["duplicates"] == ["IMG_1 (1).MOV"]
    assert report.duplicates == ["IMG_1 (1).MOV"]
    assert len(sessions(drive)) == 1
    assert drive.names_in("inbox") == []


def test_reupload_after_success_replaces_the_session(env):
    drive, process, run = env
    drive.add_video("IMG_1.MOV", data=b"same")
    sid = run().published[0]
    drive.add_video("IMG_1.MOV", data=b"same")
    run()
    assert sessions(drive) == [sid]
    assert len(process.calls) == 2
    assert drive.tree().count(f"sessions/{sid}/swing-01/01-address.jpg") == 1


def test_same_filename_different_content_makes_two_sessions(env):
    drive, process, run = env
    drive.add_video("IMG_1.MOV", data=b"monday")
    drive.add_video("IMG_1.MOV", data=b"friday")  # Drive allows the same name twice
    run()
    assert len(sessions(drive)) == 2


def test_first_and_second_failures_go_back_to_the_inbox(env):
    drive, process, run = env
    drive.add_video("bad.MOV", data=b"bad")
    process.fail_on.add("bad.MOV")
    assert run().retrying == ["bad.MOV"]
    assert attempts(drive, b"bad") == 1
    assert drive.names_in("inbox") == ["bad.MOV"]
    run()
    assert attempts(drive, b"bad") == 2


def test_third_failure_moves_to_failed_with_an_error_file(env):
    drive, process, run = env
    drive.add_video("bad.MOV", data=b"bad")
    process.fail_on.add("bad.MOV")
    run()
    run()
    assert run().failed == ["bad.MOV"]
    assert drive.names_in("failed") == ["bad.MOV", "bad.MOV.error.txt"]
    assert b"cannot decode bad.MOV" in drive.content("failed/bad.MOV.error.txt")
    assert drive.names_in("inbox") == []


def test_video_left_in_processing_is_recovered_and_retried(env):
    drive, process, run = env
    drive.add_video("IMG_1.MOV", folder="processing")
    report = run()
    assert report.recovered == ["IMG_1.MOV"]
    assert len(report.published) == 1
    assert drive.names_in("processing") == []


def test_recovery_counts_as_an_attempt(env):
    drive, process, run = env
    drive.add_video("IMG_1.MOV", folder="processing", data=b"x")
    process.fail_on.add("IMG_1.MOV")
    run()
    assert attempts(drive, b"x") == 2  # recovery, then the failure


def test_recovered_video_past_the_limit_goes_to_failed(env):
    drive, process, run = env
    import hashlib

    drive.write_text(STATE, json.dumps({"attempts": {hashlib.sha256(b"x").hexdigest(): 2}}))
    drive.add_video("IMG_1.MOV", folder="processing", data=b"x")
    assert run().failed == ["IMG_1.MOV"]


def test_reupload_of_a_failed_video_clears_it_and_starts_fresh(env):
    drive, process, run = env
    drive.add_video("bad.MOV", data=b"data")
    process.fail_on.add("bad.MOV")
    run()
    run()
    run()
    process.fail_on.clear()
    drive.add_video("bad.MOV", data=b"data")
    report = run()
    assert drive.names_in("failed") == []
    assert len(report.published) == 1
    assert attempts(drive, b"data") == 0


def test_crash_after_upload_keeps_the_previous_session(env, monkeypatch):
    drive, process, run = env
    drive.add_video("IMG_1.MOV", data=b"same")
    sid = run().published[0]
    drive.add_video("IMG_1.MOV", data=b"same")
    real_move = drive.move

    def die_on_swap(src, dst):
        if dst.endswith(f".old-{sid}"):
            raise RuntimeError("killed")
        real_move(src, dst)

    monkeypatch.setattr(drive, "move", die_on_swap)
    run()
    assert f"sessions/{sid}/swing-01/01-address.jpg" in drive.tree()


def test_crash_between_the_swap_renames_then_a_failed_upload_keeps_a_session(env, monkeypatch):
    drive, process, run = env
    drive.add_video("IMG_1.MOV", data=b"same")
    sid = run().published[0]
    real_move, real_upload = drive.move, drive.upload_tree

    def die_after_moving_the_old_one(src, dst):
        if dst == f"sessions/{sid}":
            raise RuntimeError("killed")
        real_move(src, dst)

    monkeypatch.setattr(drive, "move", die_after_moving_the_old_one)
    drive.add_video("IMG_1.MOV", data=b"same")
    run()
    assert f".old-{sid}" in sessions(drive)
    monkeypatch.setattr(drive, "move", real_move)

    def die_uploading(src, dst):
        raise RuntimeError("killed mid-upload")

    monkeypatch.setattr(drive, "upload_tree", die_uploading)
    drive.add_video("IMG_1.MOV", data=b"same")
    run()
    monkeypatch.setattr(drive, "upload_tree", real_upload)
    sweep(drive, Settings(), drive.now + timedelta(days=2))
    assert f"sessions/{sid}/swing-01/01-address.jpg" in drive.tree()


def test_leftover_temp_folder_is_replaced_on_the_next_publish(env):
    drive, process, run = env
    drive.add_video("IMG_1.MOV", data=b"same")
    sid = run().published[0]
    drive.mkdir(f"sessions/.tmp-{sid}")
    drive.add_video("IMG_1.MOV", data=b"same")
    run()
    assert sessions(drive) == [sid]


def test_one_bad_video_does_not_stop_the_others(env):
    drive, process, run = env
    drive.add_video("bad.MOV", data=b"bad")
    drive.add_video("good.MOV", data=b"good")
    process.fail_on.add("bad.MOV")
    report = run()
    assert report.retrying == ["bad.MOV"]
    assert len(report.published) == 1


def test_awkward_filenames_survive_the_round_trip(env):
    drive, process, run = env
    name = "Bob's swing #2 (café).MOV"
    drive.add_video(name, data=b"swing")
    process.fail_on.add(name)
    run()
    run()
    run()
    assert drive.names_in("failed") == sorted([name, f"{name}.error.txt"])
