"""Migration from a fake Drive to a local bare repo over file://."""

import json
import shutil
import subprocess

import pytest

from golf_etl.storage.github import GitHubPublisher
from golf_etl.storage.migrate import MigrateReport, migrate
from tests.conftest import git, tree
from tests.fake_store import FakeStore

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

MARKER = {
    "sha256": "abc",
    "sourceName": "IMG_1.MOV",
    "pipelineVersion": "v0.3.0",
    "published": "2026-10-04T12:00:00+00:00",
}


@pytest.fixture
def store():
    store = FakeStore()
    store.add("sessions/s1/session.md", b"# s1\n")
    store.add("sessions/s1/.golf-etl.json", json.dumps(MARKER).encode())
    store.add("sessions/s1/swing-01/01-address.jpg", b"address")
    store.add("sessions/s1/swing-01/06-sequence-small.jpg", b"small")
    store.add("sessions/s1/swing-01/clip.mp4", b"clip")
    store.add("sessions/s1/session.zip", b"zip")
    store.add("sessions/.old-s0/session.md", b"# s0\n")
    return store


@pytest.fixture
def pub(remote, tmp_path):
    return GitHubPublisher(f"file://{remote}", tmp_path / "work")


def has_main(remote) -> bool:
    proc = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "--verify", "main"], capture_output=True
    )
    return proc.returncode == 0


def test_migration_copies_everything_but_clips_and_zips_then_deletes_from_drive(
    store, pub, remote, tmp_path
):
    report = migrate(store, pub, tmp_path / "scratch", dry_run=False)
    assert report == MigrateReport(copied=["s1"], deleted=["s1"], kept=[], blocked=[])
    assert tree(remote) == {
        "sessions/s1/.golf-etl.json",
        "sessions/s1/session.md",
        "sessions/s1/swing-01/01-address.jpg",
    }
    pushed = git("--git-dir", str(remote), "show", "main:sessions/s1/.golf-etl.json")
    assert json.loads(pushed) == MARKER
    assert git("--git-dir", str(remote), "show", "main:sessions/s1/session.md") == "# s1\n"
    # Dot folders are not sessions, and the sessions folder itself stays.
    assert store.tree() == ["sessions/.old-s0/session.md"]
    assert [e.path for e in store.list("") if e.is_dir] == ["sessions"]
    assert list((tmp_path / "scratch").iterdir()) == []


def test_dry_run_copies_and_deletes_nothing(store, pub, remote, tmp_path):
    before = store.tree()
    report = migrate(store, pub, tmp_path / "scratch", dry_run=True)
    assert report == MigrateReport(copied=["s1"], deleted=["s1"], kept=[], blocked=[])
    assert not has_main(remote)
    assert store.tree() == before
    assert store.deleted == []


def test_migration_refuses_while_the_inbox_or_failed_folder_has_files(store, pub, remote, tmp_path):
    store.add_video("IMG_2.MOV")
    store.add_video("IMG_3.MOV", folder="failed")
    store.add("failed/IMG_3.MOV.error.txt", b"IMG_3.MOV failed 3 times.")
    before = store.tree()
    report = migrate(store, pub, tmp_path / "scratch", dry_run=False)
    assert report == MigrateReport(
        copied=[],
        deleted=[],
        kept=[],
        blocked=["inbox/IMG_2.MOV", "failed/IMG_3.MOV", "failed/IMG_3.MOV.error.txt"],
    )
    assert not has_main(remote)
    assert store.tree() == before


def test_migration_keeps_a_drive_session_whose_push_is_missing_a_file(
    store, pub, remote, tmp_path, monkeypatch
):
    published_files = pub.published_files

    def missing_a_frame(session_id):
        files = published_files(session_id)
        del files["swing-01/01-address.jpg"]
        return files

    monkeypatch.setattr(pub, "published_files", missing_a_frame)
    before = store.tree()
    report = migrate(store, pub, tmp_path / "scratch", dry_run=False)
    assert report == MigrateReport(copied=["s1"], deleted=[], kept=["s1"], blocked=[])
    assert store.tree() == before


def test_migration_keeps_a_drive_session_whose_pushed_marker_differs(
    store, pub, remote, tmp_path, monkeypatch
):
    publish = pub.publish
    monkeypatch.setattr(pub, "publish", lambda d, sid, m: publish(d, sid, m | {"sha256": "def"}))
    before = store.tree()
    report = migrate(store, pub, tmp_path / "scratch", dry_run=False)
    assert report == MigrateReport(copied=["s1"], deleted=[], kept=["s1"], blocked=[])
    assert store.tree() == before


def test_migration_keeps_a_drive_session_without_a_marker(store, pub, remote, tmp_path):
    store.delete("sessions/s1/.golf-etl.json")
    before = store.tree()
    report = migrate(store, pub, tmp_path / "scratch", dry_run=False)
    assert report == MigrateReport(copied=[], deleted=[], kept=["s1"], blocked=[])
    assert not has_main(remote)
    assert store.tree() == before
