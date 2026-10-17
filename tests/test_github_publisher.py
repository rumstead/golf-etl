"""GitHubPublisher against a local bare repo over file://: the real git binary, no GitHub."""

import json
import shutil
import subprocess

import pytest

from golf_etl.storage.github import GitHubPublisher
from golf_etl.storage.publish import marker
from tests.conftest import git, tree

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

S1 = {
    "sessions/s1/.golf-etl.json",
    "sessions/s1/session.md",
    "sessions/s1/swing-01/06-sequence.jpg",
    "sessions/s1/swing-01/pose.csv",
}
S2 = {p.replace("/s1/", "/s2/") for p in S1}


@pytest.fixture
def pub(remote, tmp_path):
    return GitHubPublisher(f"file://{remote}", tmp_path / "work")


def session(tmp_path, sid):
    root = tmp_path / "src" / sid
    (root / "swing-01").mkdir(parents=True)
    (root / "session.md").write_text(f"# {sid}\n")
    (root / "swing-01" / "06-sequence.jpg").write_bytes(f"sequence {sid}".encode())
    (root / "swing-01" / "06-sequence-small.jpg").write_bytes(b"small")
    (root / "swing-01" / "clip.mp4").write_bytes(b"clip")
    (root / "swing-01" / "pose.csv").write_text(f"frame,session\n1,{sid}\n")
    return root


def commits(remote) -> int:
    return int(git("--git-dir", str(remote), "rev-list", "--count", "main"))


def test_first_publish_to_an_empty_repo_creates_main(pub, remote, tmp_path):
    pub.publish(session(tmp_path, "s1"), "s1", marker("abc", "IMG_1.MOV", "test"))
    assert tree(remote) == S1


def test_clips_and_small_copies_are_not_published(pub, remote, tmp_path):
    pub.publish(session(tmp_path, "s1"), "s1", marker("abc", "IMG_1.MOV", "test"))
    pub.publish(session(tmp_path, "s2"), "s2", marker("def", "IMG_2.MOV", "test"))
    assert tree(remote) == S1 | S2
    names = {p.rsplit("/", 1)[1] for p in tree(remote)}
    assert "clip.mp4" not in names
    assert "06-sequence-small.jpg" not in names


def test_second_session_keeps_the_first(pub, remote, tmp_path):
    pub.publish(session(tmp_path, "s1"), "s1", marker("abc", "IMG_1.MOV", "test"))
    pub.publish(session(tmp_path, "s2"), "s2", marker("def", "IMG_2.MOV", "test"))
    assert tree(remote) == S1 | S2
    assert commits(remote) == 2


def test_republishing_a_session_replaces_its_folder(pub, remote, tmp_path):
    s1 = session(tmp_path, "s1")
    pub.publish(s1, "s1", marker("abc", "IMG_1.MOV", "test"))
    pub.publish(session(tmp_path, "s2"), "s2", marker("def", "IMG_2.MOV", "test"))
    (s1 / "swing-01" / "pose.csv").unlink()
    pub.publish(s1, "s1", marker("abc", "IMG_1.MOV", "test"))
    assert tree(remote) == (S1 - {"sessions/s1/swing-01/pose.csv"}) | S2
    assert commits(remote) == 3


def test_republishing_the_same_files_makes_no_commit(pub, remote, tmp_path):
    s1 = session(tmp_path, "s1")
    m = marker("abc", "IMG_1.MOV", "test")
    pub.publish(s1, "s1", m)
    pub.publish(s1, "s1", m)
    assert tree(remote) == S1
    assert commits(remote) == 1


def test_publish_only_checks_out_its_own_session(pub, remote, tmp_path):
    pub.publish(session(tmp_path, "s1"), "s1", marker("abc", "IMG_1.MOV", "test"))
    pub.publish(session(tmp_path, "s2"), "s2", marker("def", "IMG_2.MOV", "test"))
    pub.publish(session(tmp_path, "s3"), "s3", marker("ghi", "IMG_3.MOV", "test"))
    work = tmp_path / "work"
    assert {p.name for p in (work / "sessions").iterdir()} == {"s3"}
    assert git("-C", str(work), "rev-parse", "--is-shallow-repository").strip() == "true"
    # The other sessions' blobs were never downloaded.
    out = git("-C", str(work), "rev-list", "--objects", "--missing=print", "HEAD")
    missing = {line[1:] for line in out.splitlines() if line.startswith("?")}
    listing = git("--git-dir", str(remote), "ls-tree", "-r", "main", "sessions/s1", "sessions/s2")
    assert missing == {line.split()[2] for line in listing.splitlines()}
    assert commits(remote) == 3


def test_published_files_reports_sizes_from_the_remote(pub, tmp_path):
    s1 = session(tmp_path, "s1")
    m = marker("abc", "IMG_1.MOV", "test")
    pub.publish(s1, "s1", m)
    assert pub.published_files("s1") == {
        ".golf-etl.json": len(json.dumps(m, indent=2).encode()),
        "session.md": (s1 / "session.md").stat().st_size,
        "swing-01/06-sequence.jpg": (s1 / "swing-01" / "06-sequence.jpg").stat().st_size,
        "swing-01/pose.csv": (s1 / "swing-01" / "pose.csv").stat().st_size,
    }


def test_a_rejected_push_raises(pub, remote, tmp_path):
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(RuntimeError, match="push"):
        pub.publish(session(tmp_path, "s1"), "s1", marker("abc", "IMG_1.MOV", "test"))
    unborn = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "--verify", "main"], capture_output=True
    )
    assert unborn.returncode != 0
