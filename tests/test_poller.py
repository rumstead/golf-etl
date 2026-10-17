import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest

from golf_etl.config import Settings
from golf_etl.pipeline import SessionResult
from golf_etl.storage.poller import Poller
from golf_etl.storage.publish import DrivePublisher
from golf_etl.storage.retention import sweep_sessions
from golf_etl.storage.store import STATE, State
from tests.fake_store import FakeStore


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
    store = FakeStore()
    process = FakeProcess()

    def run():
        return Poller(
            store,
            DrivePublisher(store, Settings()),
            Settings(),
            process,
            tmp_path / "scratch",
            "test",
        ).run()

    return store, process, run


def sessions(store):
    return store.names_in("sessions")


def attempts(store, name_data: bytes) -> int:
    return State.load(store).attempts.get(hashlib.sha256(name_data).hexdigest(), 0)


def test_first_run_creates_the_folder_layout(env):
    store, process, run = env
    run()
    assert {"inbox", "processing", "failed"} <= set(store.dirs)


def test_video_becomes_a_session_and_the_original_is_deleted(env):
    store, process, run = env
    store.add_video("IMG_1.MOV", data=b"swing one")
    report = run()
    assert len(report.published) == 1
    sid = report.published[0]
    assert sessions(store) == [sid]
    assert store.names_in("inbox") == []
    assert store.names_in("processing") == []
    assert "IMG_1.MOV" in store.deleted
    assert f"sessions/{sid}/swing-01/01-address.jpg" in store.tree()


def test_session_folder_has_a_marker_with_the_checksum(env):
    store, process, run = env
    store.add_video("IMG_1.MOV", data=b"swing one")
    sid = run().published[0]
    marker = json.loads(store.read_text(f"sessions/{sid}/.golf-etl.json"))
    assert marker["sha256"] == process.calls[0]["checksum"]
    assert marker["sourceName"] == "IMG_1.MOV"
    assert marker["pipelineVersion"] == "test"


def test_non_video_files_are_left_alone(env):
    store, process, run = env
    store.add_video("IMG_1.MOV")
    store.add("inbox/strike.jpg", b"photo")
    run()
    assert store.names_in("inbox") == ["strike.jpg"]


def test_duplicates_in_one_poll_are_processed_once(env):
    store, process, run = env
    store.add_video("IMG_1.MOV", data=b"same", modified=store.now)
    store.add_video("IMG_1 (1).MOV", data=b"same", modified=store.now + timedelta(seconds=5))
    report = run()
    assert len(process.calls) == 1
    assert process.calls[0]["duplicates"] == ["IMG_1 (1).MOV"]
    assert report.duplicates == ["IMG_1 (1).MOV"]
    assert len(sessions(store)) == 1
    assert store.names_in("inbox") == []


def test_reupload_after_success_replaces_the_session(env):
    store, process, run = env
    store.add_video("IMG_1.MOV", data=b"same")
    sid = run().published[0]
    store.add_video("IMG_1.MOV", data=b"same")
    run()
    assert sessions(store) == [sid]
    assert len(process.calls) == 2
    assert store.tree().count(f"sessions/{sid}/swing-01/01-address.jpg") == 1


def test_same_filename_different_content_makes_two_sessions(env):
    store, process, run = env
    store.add_video("IMG_1.MOV", data=b"monday")
    store.add_video("IMG_1.MOV", data=b"friday")  # Drive allows the same name twice
    run()
    assert len(sessions(store)) == 2


def test_first_and_second_failures_go_back_to_the_inbox(env):
    store, process, run = env
    store.add_video("bad.MOV", data=b"bad")
    process.fail_on.add("bad.MOV")
    assert run().retrying == ["bad.MOV"]
    assert attempts(store, b"bad") == 1
    assert store.names_in("inbox") == ["bad.MOV"]
    run()
    assert attempts(store, b"bad") == 2


def test_third_failure_moves_to_failed_with_an_error_file(env):
    store, process, run = env
    store.add_video("bad.MOV", data=b"bad")
    process.fail_on.add("bad.MOV")
    run()
    run()
    assert run().failed == ["bad.MOV"]
    assert store.names_in("failed") == ["bad.MOV", "bad.MOV.error.txt"]
    assert b"cannot decode bad.MOV" in store.content("failed/bad.MOV.error.txt")
    assert store.names_in("inbox") == []


def test_video_left_in_processing_is_recovered_and_retried(env):
    store, process, run = env
    store.add_video("IMG_1.MOV", folder="processing")
    report = run()
    assert report.recovered == ["IMG_1.MOV"]
    assert len(report.published) == 1
    assert store.names_in("processing") == []


def test_recovery_counts_as_an_attempt(env):
    store, process, run = env
    store.add_video("IMG_1.MOV", folder="processing", data=b"x")
    process.fail_on.add("IMG_1.MOV")
    run()
    assert attempts(store, b"x") == 2  # recovery, then the failure


def test_recovered_video_past_the_limit_goes_to_failed(env):
    store, process, run = env
    store.write_text(STATE, json.dumps({"attempts": {hashlib.sha256(b"x").hexdigest(): 2}}))
    store.add_video("IMG_1.MOV", folder="processing", data=b"x")
    assert run().failed == ["IMG_1.MOV"]


def test_reupload_of_a_failed_video_clears_it_and_starts_fresh(env):
    store, process, run = env
    store.add_video("bad.MOV", data=b"data")
    process.fail_on.add("bad.MOV")
    run()
    run()
    run()
    process.fail_on.clear()
    store.add_video("bad.MOV", data=b"data")
    report = run()
    assert store.names_in("failed") == []
    assert len(report.published) == 1
    assert attempts(store, b"data") == 0


def test_crash_after_upload_keeps_the_previous_session(env, monkeypatch):
    store, process, run = env
    store.add_video("IMG_1.MOV", data=b"same")
    sid = run().published[0]
    store.add_video("IMG_1.MOV", data=b"same")
    real_move = store.move

    def die_on_swap(src, dst):
        if dst.endswith(f".old-{sid}"):
            raise RuntimeError("killed")
        real_move(src, dst)

    monkeypatch.setattr(store, "move", die_on_swap)
    run()
    assert f"sessions/{sid}/swing-01/01-address.jpg" in store.tree()


def test_crash_between_the_swap_renames_then_a_failed_upload_keeps_a_session(env, monkeypatch):
    store, process, run = env
    store.add_video("IMG_1.MOV", data=b"same")
    sid = run().published[0]
    real_move, real_upload = store.move, store.upload_tree

    def die_after_moving_the_old_one(src, dst):
        if dst == f"sessions/{sid}":
            raise RuntimeError("killed")
        real_move(src, dst)

    monkeypatch.setattr(store, "move", die_after_moving_the_old_one)
    store.add_video("IMG_1.MOV", data=b"same")
    run()
    assert f".old-{sid}" in sessions(store)
    monkeypatch.setattr(store, "move", real_move)

    def die_uploading(src, dst):
        raise RuntimeError("killed mid-upload")

    monkeypatch.setattr(store, "upload_tree", die_uploading)
    store.add_video("IMG_1.MOV", data=b"same")
    run()
    monkeypatch.setattr(store, "upload_tree", real_upload)
    sweep_sessions(store, Settings(), store.now + timedelta(days=2))
    assert f"sessions/{sid}/swing-01/01-address.jpg" in store.tree()


def test_leftover_temp_folder_is_replaced_on_the_next_publish(env):
    store, process, run = env
    store.add_video("IMG_1.MOV", data=b"same")
    sid = run().published[0]
    store.mkdir(f"sessions/.tmp-{sid}")
    store.add_video("IMG_1.MOV", data=b"same")
    run()
    assert sessions(store) == [sid]


def test_one_bad_video_does_not_stop_the_others(env):
    store, process, run = env
    store.add_video("bad.MOV", data=b"bad")
    store.add_video("good.MOV", data=b"good")
    process.fail_on.add("bad.MOV")
    report = run()
    assert report.retrying == ["bad.MOV"]
    assert len(report.published) == 1


def test_awkward_filenames_survive_the_round_trip(env):
    store, process, run = env
    name = "Bob's swing #2 (café).MOV"
    store.add_video(name, data=b"swing")
    process.fail_on.add(name)
    run()
    run()
    run()
    assert store.names_in("failed") == sorted([name, f"{name}.error.txt"])


def test_poller_publishes_through_the_publisher(tmp_path):
    class Recorder:
        def __init__(self):
            self.calls = []

        def publish(self, session_dir, session_id, marker):
            self.calls.append(
                (session_id, marker["sha256"], sorted(p.name for p in session_dir.iterdir()))
            )

        def sweep(self, now):
            raise AssertionError("the poller does not sweep")

    store, rec = FakeStore(), Recorder()
    store.add_video("a.mov", data=b"aaa")
    Poller(store, rec, Settings(), FakeProcess(), tmp_path / "s", "test").run()
    sha = hashlib.sha256(b"aaa").hexdigest()
    assert rec.calls == [(f"2026-10-04-1200-{sha[:8]}", sha, ["session.md", "swing-01"])]
    assert store.names_in("sessions") == []


def test_status_tracks_a_video_from_processing_to_published(env):
    store, process, run = env
    store.add_video("a.mov", data=b"aaa")
    run()
    [item] = State.load(store).recent
    assert (item["name"], item["status"]) == ("a.mov", "published")
    assert item["session"].endswith(hashlib.sha256(b"aaa").hexdigest()[:8])


def test_status_shows_retrying_then_failed_with_the_error(env):
    store, process, run = env
    store.add_video("bad.mov", data=b"bad")
    process.fail_on.add("bad.mov")
    run()
    assert State.load(store).recent[0]["status"] == "retrying"
    run()
    run()
    item = State.load(store).recent[0]
    assert item["status"] == "failed" and "cannot decode bad.mov" in item["error"]


def test_status_keeps_the_newest_fifty(env):
    store, process, run = env
    for i in range(55):
        store.add_video(f"v{i}.mov", data=f"v{i}".encode())
    run()
    assert len(State.load(store).recent) == 50
