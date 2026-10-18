import csv

import numpy as np
import pytest

from golf_etl.pose import toes_direction, write_pose_csv
from tests.ball_clips import address_pose
from tests.poses import still_track, swing_track


def test_hand_speed_peaks_near_impact():
    track = swing_track(10.0)
    speed = track.hand_speed()
    assert track.times[int(np.nanargmax(speed))] == pytest.approx(10.0, abs=0.05)


def test_still_person_has_no_hand_speed():
    assert np.nanmax(still_track(5.0).hand_speed()) == pytest.approx(0.0)


def test_hand_height_highest_at_top():
    track = swing_track(10.0)
    before = track.times < 10.0
    top = track.times[before][int(np.argmax(track.hand_height()[before]))]
    assert top == pytest.approx(9.6, abs=0.05)


def test_nearest_returns_closest_sample():
    track = swing_track(10.0)
    lm = track.nearest(10.001)
    assert lm.shape == (33, 3)


def test_missing_pose_is_nan_speed():
    track = swing_track(10.0, missing=(8.0, 8.5))
    speed = track.hand_speed()
    gap = (track.times > 8.05) & (track.times < 8.45)
    assert np.all(np.isnan(speed[gap]))


def read_csv(path):
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def test_pose_csv_has_one_row_per_sample_and_the_named_joints(tmp_path):
    track = swing_track(10.0, fps=60)
    path = tmp_path / "pose.csv"
    write_pose_csv(track, path, impact_s=10.0, clip_start_s=7.5, clip_fps=120)
    rows = read_csv(path)
    assert len(rows) == len(track.times)
    assert list(rows[0])[:3] == ["time_s", "t_ms", "clip_frame"]
    for joint in ("nose", "left_shoulder", "right_wrist", "left_hip", "right_knee", "left_heel"):
        assert {f"{joint}_x", f"{joint}_y", f"{joint}_vis"} <= set(rows[0])
    assert float(rows[0]["left_wrist_y"]) == pytest.approx(0.6, abs=0.001)


def test_pose_csv_times_are_relative_to_impact_and_the_clip(tmp_path):
    track = swing_track(10.0, fps=60)
    path = tmp_path / "pose.csv"
    write_pose_csv(track, path, impact_s=10.0, clip_start_s=7.5, clip_fps=120)
    rows = read_csv(path)
    assert float(rows[0]["time_s"]) == pytest.approx(7.5, abs=0.001)
    assert int(rows[0]["t_ms"]) == -2500
    assert int(rows[0]["clip_frame"]) == 0
    assert int(rows[150]["t_ms"]) == 0  # 2.5s in at 60fps
    assert int(rows[150]["clip_frame"]) == 300  # 2.5s in at 120fps


def test_pose_csv_leaves_cells_empty_without_a_pose(tmp_path):
    track = swing_track(10.0, fps=60, missing=(9.0, 9.5))
    path = tmp_path / "pose.csv"
    write_pose_csv(track, path, impact_s=10.0, clip_start_s=7.5, clip_fps=120)
    rows = read_csv(path)
    gap = [r for r in rows if 9.05 <= float(r["time_s"]) < 9.45]
    assert gap and all(r["nose_x"] == "" and r["left_heel_vis"] == "" for r in gap)
    assert "nan" not in path.read_text().lower()


def test_pose_csv_for_a_4s_clip_reads_in_one_drive_call(tmp_path):
    path = tmp_path / "pose.csv"
    write_pose_csv(swing_track(10.0, fps=60), path, impact_s=10.0, clip_start_s=7.5, clip_fps=120)
    assert path.stat().st_size < 80_000


@pytest.mark.parametrize("toes", [1, -1, 0])
def test_toes_direction(toes):
    assert toes_direction(address_pose(toes=toes), 9 / 16) == toes


def test_toes_direction_without_feet_is_none():
    pose = address_pose()
    pose[29:33] = np.nan  # heels and toes
    assert toes_direction(pose, 9 / 16) is None
