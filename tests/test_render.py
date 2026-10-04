import numpy as np
import pytest
from PIL import Image

from golf_etl import render
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


def test_draw_pose_marks_pixels_and_skips_missing_pose():
    lm = swing_track(10.0).nearest(10.0)
    lm[[11, 12, 13, 14, 23, 24, 25, 26], :2] = 0.5
    assert (render.draw_pose(frame(720, 1280), lm) != 90).any()
    empty = np.full((33, 3), np.nan)
    assert (render.draw_pose(frame(720, 1280), empty) == 90).all()


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


def test_save_jpeg_is_rgb_without_exif(tmp_path):
    path = tmp_path / "f.jpg"
    render.save_jpeg(frame(100, 200), path, 90)
    with Image.open(path) as img:
        assert img.mode == "RGB"
        assert img.format == "JPEG"
        assert not img.getexif()
