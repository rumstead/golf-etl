import shutil

import pytest

from golf_etl.cli import main
from golf_etl.sim import FIELDS, Glyphs, value_boxes
from tests.conftest import make_video_with_clicks, tree
from tests.sim_fixtures import load


def test_unknown_command_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == 2


@pytest.mark.media
def test_process_writes_a_session_locally(tmp_path, capsys):
    video = make_video_with_clicks(tmp_path / "empty.mp4", [3.0], seconds=6.0, size="640x360")
    assert main(["process", str(video), "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "0 swings, 1 rejected" in out
    assert len(list((tmp_path / "out").glob("*/session.md"))) == 1


def test_poll_drive_on_gdrive_without_a_token_exits_2(monkeypatch, capsys):
    monkeypatch.setenv("GOLF_STORAGE", "drive")
    monkeypatch.delenv("RCLONE_CONFIG_GDRIVE_TOKEN", raising=False)
    monkeypatch.delenv("GOLF_REMOTE", raising=False)
    assert main(["poll-drive"]) == 2
    assert "RCLONE_CONFIG_GDRIVE_TOKEN" in capsys.readouterr().err


@pytest.mark.media
def test_poll_drive_against_a_local_folder_publishes_a_session(tmp_path, monkeypatch):
    if shutil.which("rclone") is None:
        pytest.skip("rclone not installed")
    root = tmp_path / "golf"
    (root / "inbox").mkdir(parents=True)
    make_video_with_clicks(root / "inbox" / "empty.mp4", [3.0], seconds=6.0, size="640x360")
    monkeypatch.setenv("GOLF_STORAGE", "drive")
    monkeypatch.setenv("GOLF_REMOTE", str(root))
    monkeypatch.setenv("GOLF_SCRATCH_DIR", str(tmp_path / "scratch"))
    assert main(["poll-drive"]) == 0
    sessions = list((root / "sessions").iterdir())
    assert len(sessions) == 1 and (sessions[0] / "session.md").exists()
    assert list((root / "inbox").glob("*.mp4")) == []  # the original is gone


def test_poll_rejects_an_unknown_storage(monkeypatch, capsys):
    monkeypatch.setenv("GOLF_STORAGE", "s3")
    assert main(["poll"]) == 2
    assert "GOLF_STORAGE must be github or drive" in capsys.readouterr().err


def test_github_poll_without_a_deploy_key_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("GOLF_STORAGE", raising=False)
    monkeypatch.delenv("GOLF_SESSIONS_REPO", raising=False)
    monkeypatch.setenv("GOLF_SSH_KEY_PATH", str(tmp_path / "missing"))
    assert main(["poll"]) == 2
    assert str(tmp_path / "missing") in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_github_poll_logs_only_the_failed_originals_sweep(remote, tmp_path, monkeypatch, caplog):
    if shutil.which("rclone") is None:
        pytest.skip("rclone not installed")
    data = tmp_path / "data"
    (data / "inbox").mkdir(parents=True)
    monkeypatch.delenv("GOLF_STORAGE", raising=False)
    monkeypatch.setenv("GOLF_INBOX_ROOT", str(data))
    monkeypatch.setenv("GOLF_SESSIONS_REPO", f"file://{remote}")
    monkeypatch.setenv("GOLF_SCRATCH_DIR", str(tmp_path / "scratch"))
    with caplog.at_level("INFO"):
        assert main(["poll"]) == 0
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("sweep:")]
    assert line == "sweep: deleted 0 failed originals"


def test_migrate_drive_without_a_deploy_key_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GOLF_REMOTE", str(tmp_path / "golf"))
    monkeypatch.delenv("GOLF_SESSIONS_REPO", raising=False)
    monkeypatch.setenv("GOLF_SSH_KEY_PATH", str(tmp_path / "missing"))
    assert main(["migrate-drive"]) == 2
    assert str(tmp_path / "missing") in capsys.readouterr().err


def test_migrate_drive_on_gdrive_without_a_token_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("RCLONE_CONFIG_GDRIVE_TOKEN", raising=False)
    monkeypatch.delenv("GOLF_REMOTE", raising=False)
    assert main(["migrate-drive"]) == 2
    assert "RCLONE_CONFIG_GDRIVE_TOKEN" in capsys.readouterr().err


@pytest.mark.media
@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_github_poll_publishes_to_the_repo_and_empties_the_inbox(remote, tmp_path, monkeypatch):
    if shutil.which("rclone") is None:
        pytest.skip("rclone not installed")
    data = tmp_path / "data"
    (data / "inbox").mkdir(parents=True)
    make_video_with_clicks(data / "inbox" / "empty.mp4", [3.0], seconds=6.0, size="640x360")
    monkeypatch.delenv("GOLF_STORAGE", raising=False)
    monkeypatch.setenv("GOLF_INBOX_ROOT", str(data))
    monkeypatch.setenv("GOLF_SESSIONS_REPO", f"file://{remote}")
    monkeypatch.setenv("GOLF_SCRATCH_DIR", str(tmp_path / "scratch"))
    assert main(["poll"]) == 0
    published = tree(remote)
    sessions = {p.split("/")[1] for p in published}
    assert len(sessions) == 1
    assert f"sessions/{sessions.pop()}/session.md" in published
    assert list((data / "inbox").glob("*.mp4")) == []  # the original is gone


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_migrate_drive_moves_a_local_folder_session_to_the_repo(
    remote, tmp_path, monkeypatch, capsys
):
    if shutil.which("rclone") is None:
        pytest.skip("rclone not installed")
    root = tmp_path / "golf"
    s1 = root / "sessions" / "s1"
    (s1 / "swing-01").mkdir(parents=True)
    (s1 / "session.md").write_text("# s1\n")
    (s1 / ".golf-etl.json").write_text('{"sha256": "abc"}')
    (s1 / "swing-01" / "01-address.jpg").write_bytes(b"address")
    (s1 / "swing-01" / "clip.mp4").write_bytes(b"clip")
    for folder in ("inbox", "processing", "failed"):
        (root / folder).mkdir()
    monkeypatch.setenv("GOLF_REMOTE", str(root))
    monkeypatch.setenv("GOLF_SESSIONS_REPO", f"file://{remote}")
    monkeypatch.setenv("GOLF_SCRATCH_DIR", str(tmp_path / "scratch"))
    assert main(["migrate-drive", "--dry-run"]) == 0
    assert s1.exists()
    assert main(["migrate-drive"]) == 0
    assert tree(remote) == {
        "sessions/s1/.golf-etl.json",
        "sessions/s1/session.md",
        "sessions/s1/swing-01/01-address.jpg",
    }
    assert list((root / "sessions").iterdir()) == []
    assert capsys.readouterr().out.splitlines() == [
        "s1: would copy, then delete from Drive",
        "dry run: 1 copied, 1 deleted, 0 kept",
        "s1: copied and deleted from Drive",
        "1 copied, 1 deleted, 0 kept",
    ]


def test_sim_glyphs_and_eval_sim_are_subcommands(tmp_path, monkeypatch, capsys):
    rgb, labels = load("4761-s1")
    boxes = value_boxes((40, 166, 40, 283))
    cells = [
        ("a.MOV", rgb[y0:y1, x0:x1], field, labels[field])
        for field, (x0, x1, y0, y1) in zip(FIELDS, boxes, strict=True)
        if labels[field]
    ]
    monkeypatch.setattr("golf_etl.sim_eval.labeled_cells", lambda path: iter(cells))
    out = tmp_path / "glyphs.npz"
    assert main(["sim-glyphs", str(tmp_path / "labels.yaml"), str(out)]) == 0
    assert len(Glyphs.load(out).labels) > 0

    used = []

    def score(path, glyphs_for):
        used.append(len(glyphs_for("a.MOV").labels))
        return 1, 0, 0, ["total: right 1, wrong 0, blank 0"]

    monkeypatch.setattr("golf_etl.sim_eval.score", score)
    assert main(["eval-sim", str(tmp_path / "labels.yaml"), "--glyphs", str(out)]) == 0
    assert main(["eval-sim", str(tmp_path / "labels.yaml")]) == 0
    assert used == [len(Glyphs.load(out).labels), 0]  # held out: no other video to learn from
    assert "total: right 1, wrong 0, blank 0" in capsys.readouterr().out
