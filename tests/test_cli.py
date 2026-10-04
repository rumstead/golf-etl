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
