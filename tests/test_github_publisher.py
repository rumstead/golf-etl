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
INDEX = {"sessions/index.md", "sessions/chain.md"}


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
    pub.publish(session(tmp_path, "s1"), "s1", marker("abc", "IMG_1.MOV", "test", "range"))
    assert tree(remote) == S1 | INDEX


def test_clips_and_small_copies_are_not_published(pub, remote, tmp_path):
    pub.publish(session(tmp_path, "s1"), "s1", marker("abc", "IMG_1.MOV", "test", "range"))
    pub.publish(session(tmp_path, "s2"), "s2", marker("def", "IMG_2.MOV", "test", "range"))
    assert tree(remote) == S1 | S2 | INDEX
    names = {p.rsplit("/", 1)[1] for p in tree(remote)}
    assert "clip.mp4" not in names
    assert "06-sequence-small.jpg" not in names


def test_second_session_keeps_the_first(pub, remote, tmp_path):
    pub.publish(session(tmp_path, "s1"), "s1", marker("abc", "IMG_1.MOV", "test", "range"))
    pub.publish(session(tmp_path, "s2"), "s2", marker("def", "IMG_2.MOV", "test", "range"))
    assert tree(remote) == S1 | S2 | INDEX
    assert commits(remote) == 2


def test_republishing_a_session_replaces_its_folder(pub, remote, tmp_path):
    s1 = session(tmp_path, "s1")
    pub.publish(s1, "s1", marker("abc", "IMG_1.MOV", "test", "range"))
    pub.publish(session(tmp_path, "s2"), "s2", marker("def", "IMG_2.MOV", "test", "range"))
    (s1 / "swing-01" / "pose.csv").unlink()
    pub.publish(s1, "s1", marker("abc", "IMG_1.MOV", "test", "range"))
    assert tree(remote) == (S1 - {"sessions/s1/swing-01/pose.csv"}) | S2 | INDEX
    assert commits(remote) == 3


def test_republishing_the_same_files_makes_no_commit(pub, remote, tmp_path):
    s1 = session(tmp_path, "s1")
    m = marker("abc", "IMG_1.MOV", "test", "range")
    pub.publish(s1, "s1", m)
    pub.publish(s1, "s1", m)
    assert tree(remote) == S1 | INDEX
    assert commits(remote) == 1


def test_publish_only_checks_out_its_own_session(pub, remote, tmp_path):
    pub.publish(session(tmp_path, "s1"), "s1", marker("abc", "IMG_1.MOV", "test", "range"))
    pub.publish(session(tmp_path, "s2"), "s2", marker("def", "IMG_2.MOV", "test", "range"))
    pub.publish(session(tmp_path, "s3"), "s3", marker("ghi", "IMG_3.MOV", "test", "range"))
    work = tmp_path / "work"
    # of the other sessions only what the index is built from is checked out
    files = {p.relative_to(work).as_posix() for p in (work / "sessions").rglob("*") if p.is_file()}
    markers = {"sessions/s1/.golf-etl.json", "sessions/s2/.golf-etl.json"}
    assert files == INDEX | markers | {p.replace("/s1/", "/s3/") for p in S1}
    assert git("-C", str(work), "rev-parse", "--is-shallow-repository").strip() == "true"
    # The other sessions' blobs were never downloaded.
    out = git("-C", str(work), "rev-list", "--objects", "--missing=print", "HEAD")
    missing = {line[1:] for line in out.splitlines() if line.startswith("?")}
    listing = git("--git-dir", str(remote), "ls-tree", "-r", "main", "sessions/s1", "sessions/s2")
    assert missing == {
        line.split()[2] for line in listing.splitlines() if not line.endswith(".golf-etl.json")
    }
    assert commits(remote) == 3


def test_the_index_has_a_row_per_session(pub, remote, tmp_path):
    s1 = session(tmp_path, "s1")
    row = {
        "venue": "sim",
        "swings": 5,
        "grades": {"good": 4, "fair": 1, "poor": 0},
        "medians": {"smash": 1.29, "club_path": 2.3, "face_to_path": -1.8, "tempo": 2.7}
        | {"side_spin": -510.0},
    }
    (s1 / "summary.json").write_text(json.dumps(row))
    pub.publish(s1, "s1", marker("abc", "IMG_1.MOV", "test", "sim"))
    pub.publish(session(tmp_path, "s2"), "s2", marker("def", "IMG_2.MOV", "test", "range"))
    index = git("--git-dir", str(remote), "show", "main:sessions/index.md")
    assert "| s1 | sim | 5 | 4 | 1 | 0 | | | 1.29 | | | 2.3 I-O | 1.8 C | | -510 | | 2.7 |" in index
    assert "| s2 | range | | | | | | | | | | | | | | | |" in index


def test_published_files_reports_sizes_from_the_remote(pub, tmp_path):
    s1 = session(tmp_path, "s1")
    m = marker("abc", "IMG_1.MOV", "test", "range")
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
        pub.publish(session(tmp_path, "s1"), "s1", marker("abc", "IMG_1.MOV", "test", "range"))
    unborn = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "--verify", "main"], capture_output=True
    )
    assert unborn.returncode != 0


def test_the_marker_carries_the_venue():
    assert marker("abc", "IMG_1.MOV", "test", "sim")["venue"] == "sim"


def test_chain_reads_the_takeaway_labels_on_the_remote(pub, remote, tmp_path):
    s1 = session(tmp_path, "s1")
    per_swing = [
        {"swing": n, "grade": "good", "view": "down the line", "strike": s, "spine_address": 36.0}
        | {"spine_impact": 36.0 - s}
        for n, s in enumerate([1.0, 1.1, 2.0, 2.2], 1)
    ]
    summary = {"captured": "2026-11-16T12:00:00+00:00", "venue": "sim", "swings": 4}
    summary |= {"grades": {"good": 4, "fair": 0, "poor": 0}, "medians": {}, "per_swing": per_swing}
    (s1 / "summary.json").write_text(json.dumps(summary))
    pub.publish(s1, "s1", marker("abc", "IMG_1.MOV", "test", "sim"))
    # the golfer adds labels to the repo by hand
    clone = tmp_path / "clone"
    git("clone", "-q", f"file://{remote}", str(clone))
    (clone / "sessions" / "takeaway-labels.csv").write_text(
        "session,swing,label,side,by,date\ns1,1,set,,owner,2026-11-16\n"
    )
    who = ["-c", "user.name=owner", "-c", "user.email=owner@localhost"]
    git("-C", str(clone), "add", "-A")
    git("-C", str(clone), *who, "commit", "-q", "-m", "labels")
    git("-C", str(clone), "push", "-q", "origin", "main")
    pub.publish(session(tmp_path, "s2"), "s2", marker("def", "IMG_2.MOV", "test", "range"))
    chain = git("--git-dir", str(remote), "show", "main:sessions/chain.md")
    assert "| level or set | kept | 1 (too few) |" in chain
    assert "sessions/takeaway-labels.csv" in tree(remote)
