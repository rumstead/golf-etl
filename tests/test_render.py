import numpy as np
import pytest
from PIL import Image

from golf_etl import render
from golf_etl.pose import LEFT_FOOT_INDEX, LEFT_HEEL, RIGHT_FOOT_INDEX, RIGHT_HEEL
from tests.ball_clips import address_pose
from tests.poses import swing_track


def frame(h=2160, w=3840, value=90):
    return np.full((h, w, 3), value, np.uint8)


def test_fit_downscales_to_long_edge_and_never_upscales():
    assert render.fit(frame(), 2560).shape == (1440, 2560, 3)
    small = frame(720, 1280)
    assert render.fit(small, 2560) is small


def test_fit_respects_the_pixel_budget():
    h, w = render.fit(frame(1620, 2592), 2560, max_pixels=3_700_000).shape[:2]
    assert max(h, w) <= 2560 and h * w <= 3_700_000


def test_label_draws_without_touching_the_input():
    src = frame(720, 1280)
    out = render.label(src, "address 12.34s")
    assert (src == 90).all()
    assert (out[:4, :4] == 0).all()  # black label box padding in the corner
    assert (out != 90).sum() > 1000


def _golfer(toes: float = 0.0) -> np.ndarray:
    """One pose in a 1280x720 frame: legs 0.35 frame heights (252px), hands at 0.5 across, and
    the toes `toes` frame widths ahead of the heels."""
    lm = swing_track(10.0).landmarks[:1].copy()
    lm[:, [LEFT_HEEL, RIGHT_HEEL], :2] = (0.5, 0.92)
    lm[:, [LEFT_FOOT_INDEX, RIGHT_FOOT_INDEX], :2] = (0.5 + toes, 0.92)
    return lm


def test_golfer_crop_face_on_leaves_a_club_length_beside_the_hands():
    x0, x1, y0, y1 = render.golfer_crop(_golfer(), (720, 1280, 3))
    assert (x0, x1) == (int(640 - 1.2 * 252), int(640 + 1.2 * 252))
    assert y0 == int(0.3 * 720 - 0.5 * 252)  # the head over the shoulders
    assert y1 == int(0.9 * 720 + 0.2 * 252)


def test_golfer_crop_down_the_line_reaches_the_ball():
    x0, x1, _, _ = render.golfer_crop(_golfer(toes=0.05), (720, 1280, 3), ball=(0.8, 0.95))
    assert x0 == int(0.45 * 1280 - 0.6 * 252)
    assert x1 == int(0.8 * 1280 + 0.3 * 252)


def test_golfer_crop_down_the_line_without_a_rest_spot_keeps_the_toe_side():
    x0, x1, _, _ = render.golfer_crop(_golfer(toes=-0.05), (720, 1280, 3))
    assert x0 == int(0.45 * 1280 - 1.3 * 252)
    assert x1 == int(0.55 * 1280 + 0.6 * 252)


def test_golfer_crop_keeps_the_club_over_the_hands_at_the_top():
    lm = swing_track(10.0).landmarks  # the hands reach 0.2 at the top
    _, _, y0, _ = render.golfer_crop(lm, (720, 1280, 3))
    assert y0 == 0  # 0.9 leg lengths over the hands is above the frame


def test_golfer_crop_is_the_whole_frame_without_a_pose():
    assert render.golfer_crop(np.full((5, 33, 3), np.nan), (720, 1280, 3)) == (0, 1280, 0, 720)


def test_grid_never_upscales():
    sheet = render.grid([frame(200, 100)] * 8, ["x"] * 8, 4, 2560, 3_700_000)
    assert sheet.shape[:2] == (400, 400)


def test_grid_of_eight_portrait_frames_fits_claude():
    sheet = render.grid([frame(1920, 1080)] * 8, [f"p{i}" for i in range(8)], 4, 2560, 3_700_000)
    h, w = sheet.shape[:2]
    assert max(h, w) <= 2560 and h * w <= 3_700_000
    assert w / h == pytest.approx((4 * 1080) / (2 * 1920), rel=0.01)  # two rows of four


def test_grid_of_eight_landscape_frames_fits_the_long_edge():
    sheet = render.grid([frame(720, 1280)] * 8, ["x"] * 8, 4, 2560, 3_700_000)
    assert sheet.shape == (720, 2560, 3)


def test_grid_pads_a_short_last_row():
    sheet = render.grid([frame(720, 1280)] * 3, ["a", "b", "c"], 2, 2560, 3_700_000)
    h, w = sheet.shape[:2]
    assert (sheet[h // 2 :, w // 2 :] == 0).all()


def test_zoom_crop_is_a_native_band_at_ankle_height():
    lm = swing_track(10.0).nearest(10.0)  # ankles at x 0.45..0.55, y 0.9
    landscape = render.zoom_crop(frame(), lm)
    assert landscape.shape == (540, 2592, 3)  # 0.25 x 2160 tall, 1.2 x 2160 wide
    portrait = render.zoom_crop(frame(1920, 1080), lm)
    assert portrait.shape == (480, 1080, 3)  # full width when the frame is narrow


def test_zoom_crop_stays_inside_the_frame_near_the_edge():
    lm = swing_track(10.0).nearest(10.0)
    lm[[27, 28], :2] = (0.99, 0.99)
    assert render.zoom_crop(frame(), lm).shape == (540, 2592, 3)


@pytest.mark.parametrize(
    ("toes", "ball_y"), [(1, 0.57), (0, 0.76)], ids=["down the line", "face-on"]
)
def test_zoom_crop_holds_the_ball(toes, ball_y):
    # down the line the ball sits near the ankles (0.62), face-on half a leg in front of them
    img = frame(1920, 1080)
    img[round(ball_y * 1920)] = 255
    lm = address_pose(ankle_y=0.62, ankle_x=(0.35, 0.65), toes=toes, hip_y=0.37)
    assert (render.zoom_crop(img, lm) == 255).any()


def test_save_jpeg_is_rgb_without_exif(tmp_path):
    path = tmp_path / "f.jpg"
    render.save_jpeg(frame(100, 200), path, 90)
    with Image.open(path) as img:
        assert img.mode == "RGB"
        assert img.format == "JPEG"
        assert not img.getexif()
