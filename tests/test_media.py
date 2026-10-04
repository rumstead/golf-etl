import subprocess
from datetime import UTC, datetime

import numpy as np
import pytest

from golf_etl import media
from tests.conftest import make_video

pytestmark = pytest.mark.media


def test_probe_reads_size_rate_audio_and_creation_time(video):
    info = media.probe(video)
    assert (info.width, info.height) == (640, 360)
    assert info.fps == pytest.approx(30.0)
    assert info.duration_s == pytest.approx(4.0, abs=0.1)
    assert info.has_audio
    assert info.creation_time == datetime(2026, 10, 1, 15, 4, tzinfo=UTC)


def test_probe_swaps_size_for_portrait_rotation(tmp_path):
    info = media.probe(make_video(tmp_path / "portrait.mp4", rotate=90))
    assert (info.width, info.height) == (360, 640)


def test_scaled_size_never_upscales():
    assert media.scaled_size(3840, 2160, 2560) == (2560, 1440)
    assert media.scaled_size(1280, 720, 2560) == (1280, 720)
    assert media.scaled_size(1081, 1921, 1920, even=True) == (1080, 1920)


def test_read_frames_window_count_and_shape(video):
    info = media.probe(video)
    frames = list(media.read_frames(video, info, 1.0, 2.0, long_edge=320))
    assert len(frames) == pytest.approx(30, abs=1)
    t0, img = frames[0]
    assert t0 == pytest.approx(1.0)
    assert img.shape == (180, 320, 3) and img.dtype == np.uint8


def test_read_frames_caps_fps(video):
    info = media.probe(video)
    frames = list(media.read_frames(video, info, 0.0, 1.0, long_edge=320, max_fps=10))
    assert len(frames) == pytest.approx(10, abs=1)


def test_read_frames_clamps_to_duration(video):
    info = media.probe(video)
    assert list(media.read_frames(video, info, 5.0, 6.0)) == []


def test_read_audio_is_mono_at_requested_rate(video):
    samples = media.read_audio(video, media.probe(video), 48000)
    assert samples.dtype == np.float32
    assert len(samples) == pytest.approx(4 * 48000, rel=0.02)


def test_encode_clip_is_cut_and_scaled(tmp_path, video):
    info = media.probe(video)
    out = tmp_path / "out.mp4"
    media.encode_clip(video, info, 1.0, 3.0, out, long_edge=320, crf=30)
    clip = media.probe(out)
    assert (clip.width, clip.height) == (320, 180)
    assert clip.duration_s == pytest.approx(2.0, abs=0.15)


def test_pick_audio_skips_the_undecodable_iphone_track():
    # stream layout of an iPhone 17 slo-mo .mov: APAC spatial audio first, then AAC
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc"},
        {"index": 1, "codec_type": "audio", "codec_tag_string": "apac"},
        {"index": 2, "codec_type": "audio", "codec_name": "aac"},
        {"index": 3, "codec_type": "data", "codec_tag_string": "mebx"},
    ]
    assert media.pick_audio(streams) == 2
    assert media.pick_audio(streams[:2]) is None


def test_hdr_video_is_detected_and_tone_mapped(tmp_path):
    path = tmp_path / "hlg.mp4"
    x265 = "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:d=1"]
        + ["-vf", "format=yuv420p10le", "-c:v", "libx265", "-x265-params", x265, str(path)],
        check=True,
    )
    info = media.probe(path)
    assert info.hdr
    frames = list(media.read_frames(path, info, 0.0, 0.5))
    assert frames and frames[0][1].shape == (360, 640, 3)
    out = tmp_path / "clip.mp4"
    media.encode_clip(path, info, 0.0, 1.0, out, long_edge=320, crf=30)
    assert not media.probe(out).hdr


def test_read_frames_raises_when_ffmpeg_fails(tmp_path):
    bogus = tmp_path / "bogus.mp4"
    bogus.write_text("not a video")
    info = media.VideoInfo(2.0, 30.0, 64, 36, None, None)
    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        list(media.read_frames(bogus, info, 0.0, 1.0))


def test_read_frames_can_stop_early_without_an_error(video):
    frames = media.read_frames(video, media.probe(video), 0.0, 3.0)
    next(frames)
    frames.close()
