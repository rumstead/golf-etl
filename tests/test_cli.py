import pytest

from golf_etl.cli import main
from tests.conftest import make_video_with_clicks


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
    monkeypatch.delenv("RCLONE_CONFIG_GDRIVE_TOKEN", raising=False)
    monkeypatch.delenv("GOLF_REMOTE", raising=False)
    assert main(["poll-drive"]) == 2
    assert "RCLONE_CONFIG_GDRIVE_TOKEN" in capsys.readouterr().err


@pytest.mark.media
def test_poll_drive_against_a_local_folder_publishes_a_session(tmp_path, monkeypatch):
    import shutil

    if shutil.which("rclone") is None:
        pytest.skip("rclone not installed")
    root = tmp_path / "golf"
    (root / "inbox").mkdir(parents=True)
    make_video_with_clicks(root / "inbox" / "empty.mp4", [3.0], seconds=6.0, size="640x360")
    monkeypatch.setenv("GOLF_REMOTE", str(root))
    monkeypatch.setenv("GOLF_SCRATCH_DIR", str(tmp_path / "scratch"))
    assert main(["poll-drive"]) == 0
    sessions = list((root / "sessions").iterdir())
    assert len(sessions) == 1 and (sessions[0] / "session.md").exists()
    assert list((root / "inbox").glob("*.mp4")) == []  # the original is gone
