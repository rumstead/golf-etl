"""Session naming and the session.md summary."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from golf_etl.ball import BallFlight, summary_line
from golf_etl.detect import Rejected, Swing
from golf_etl.frames import Timing

_GUIDE_INTRO = """## How to read this session

- Each `swing-NN/` folder is one swing, in the order hit.
- Start with `06-sequence.jpg`: the whole swing in one image, read left to right, top row \
then bottom row: address, takeaway, halfway back, top, transition, impact, follow-through, finish. \
Takeaway is the hands reaching hip height going back and halfway back is the hands reaching \
shoulder height, so those panels show the same body position on every swing. Transition and \
follow-through are the same short time before and after impact on every swing.
- `05-impact-zoom.jpg` is the ball area at full resolution for the frames just before, at, \
and after impact, top to bottom. Use it for contact, shaft lean, and strike.
"""

_DRIVE_COPIES = """\
- `06-sequence-small.jpg`, `05-impact-zoom-small.jpg`, and `07-after-shot-small.jpg` are \
the same images under 72KB, small enough to read through the Drive connector. Use them when \
the full-size image does not open.
"""

_AFTER_SHOT = """\
- `07-after-shot.jpg` is a full-resolution frame a few seconds after impact. On a simulator \
or launch monitor its screen shows this shot's numbers (club path, face, speeds, spin, carry). \
Numbers visible in any other frame belong to the previous shot.
"""

_DRIVE_CLIP = "- `clip.mp4` is the swing as video.\n"

_POSE = """\
- `pose.csv` is the pose estimate as numbers, one row per sample (up to 60 a second): time in \
the video, ms from impact, {frame}, then x, y and visibility for the nose, \
shoulders, wrists, hips, knees, and heels. x and y are fractions of the frame width and height \
from the top left; empty cells mean no pose. Use it to measure hip turn, head movement, and \
hand height instead of estimating from the images.
- Every image has its position and its time in the video in the top-left corner.
- The Timing table gives takeaway start (the hands leaving address), top, and impact as \
{numbers} with their time in the video, plus backswing and downswing time and \
tempo (backswing over downswing). When the hands are not seen at the top (out of frame) the \
top is not known and the durations are left blank.
- The skeleton lines on the sequence sheet are a pose estimate; trust the photo over the lines.
"""


def guide(storage: str) -> str:
    """The reading guide for what the publisher leaves in the session folder."""
    if storage == "drive":
        return (
            _GUIDE_INTRO
            + _DRIVE_COPIES
            + _AFTER_SHOT
            + _DRIVE_CLIP
            + _POSE.format(frame="the nearest `clip.mp4` frame", numbers="`clip.mp4` frame numbers")
        )
    return (
        _GUIDE_INTRO
        + _AFTER_SHOT
        + _POSE.format(
            frame="the frame number in the swing",
            numbers="frame numbers counted from 2.5s before impact",
        )
    )


BALL_GUIDE = """\
- Ball px/frame, Start dir and Grade come from following the ball in the swing video. \
Ball px/frame is how fast it leaves the club, in pixels per video frame, so compare it only \
within this session. Grade compares it to the session median: good is near the median or \
faster, poor is well under it, fair is between; `ball.json` in each swing folder has the \
thresholds. \
Start dir is sideways over up: negative starts left of the camera line, positive right. \
A `lost:` track says why the ball could not be followed, and that swing has no grade.
"""


def session_id(captured: datetime, checksum: str) -> str:
    return f"{captured:%Y-%m-%d-%H%M}-{checksum[:8]}"


@dataclass
class SessionSummary:
    session_id: str
    source_name: str
    captured: datetime
    checksum: str
    version: str
    swings: list[Swing]
    rejected: list[Rejected]
    duplicates: list[str] = field(default_factory=list)
    balls: list[BallFlight] = field(default_factory=list)  # one per swing, in swing order
    ball_median: float | None = None
    timings: list[Timing] = field(default_factory=list)  # one per swing, in order
    storage: str = "github"


def video_time(t: float) -> str:
    """A time in the video, read like the Photos scrubber: seconds, then minutes past a minute."""
    if t < 60:
        return f"{t:.2f}s"
    tenths = round(t * 10)
    m, rem = divmod(tenths, 600)
    return f"{m}:{rem / 10:04.1f}"


def _cell(value: float | None, fmt: str) -> str:
    return "" if value is None else format(value, fmt)


def write_session_md(path: Path, s: SessionSummary) -> None:
    lines = [
        f"# Session {s.session_id}",
        "",
        f"- Source: {s.source_name}",
        f"- Captured: {s.captured:%Y-%m-%d %H:%M %Z}".rstrip(),
        f"- Checksum: {s.checksum}",
        f"- Pipeline: {s.version}",
        f"- Swings: {len(s.swings)}",
        "",
    ]
    if s.swings and s.balls:
        lines += [guide(s.storage) + BALL_GUIDE, summary_line(s.balls, s.ball_median), ""]
        lines += [
            "| Swing | Impact | Strength | Hand speed peak "
            "| Ball px/frame | Start dir | Grade | Track |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for n, (swing, b) in enumerate(zip(s.swings, s.balls, strict=True), 1):
            cells = [
                f"swing-{n:02d}",
                video_time(swing.impact_s),
                f"{swing.strength:.2f}",
                f"{swing.peak_offset_ms:+.0f}ms",
                _cell(b.ball_px_per_frame, ".1f"),
                _cell(b.start_dir, ".2f"),
                b.grade or "",
                b.track,
            ]
            lines.append("|" + "|".join(f" {c} " if c else " " for c in cells) + "|")
    elif s.swings:
        lines += [guide(s.storage)]
        lines += ["| Swing | Impact | Strength | Hand speed peak |", "|---|---|---|---|"]
        for n, swing in enumerate(s.swings, 1):
            lines.append(
                f"| swing-{n:02d} | {video_time(swing.impact_s)} | {swing.strength:.2f} "
                f"| {swing.peak_offset_ms:+.0f}ms |"
            )
    else:
        lines.append("No swings detected.")
    if s.swings and s.timings:
        lines += [
            "",
            "## Timing",
            "",
            "| Swing | Takeaway start | Top | Impact | Backswing | Downswing | Tempo |",
            "|---|---|---|---|---|---|---|",
        ]
        for n, t in enumerate(s.timings, 1):
            start = f"{t.takeaway_start_frame} ({video_time(t.takeaway_start_s)})"
            impact = f"{t.impact_frame} ({video_time(t.impact_s)})"
            if t.top_in_frame:
                top = f"{t.top_frame} ({video_time(t.top_s)})"
                durations = f"{t.backswing_s:.2f}s | {t.downswing_s:.2f}s | {t.tempo:.1f}"
            else:
                top, durations = "hands not seen at the top", "| |"
            lines.append(f"| swing-{n:02d} | {start} | {top} | {impact} | {durations} |")
    if s.rejected:
        lines += ["", "## Rejected candidates", "", "| Time | Strength | Reason |", "|---|---|---|"]
        lines += [f"| {video_time(r.time_s)} | {r.strength:.2f} | {r.reason} |" for r in s.rejected]
    if s.duplicates:
        lines += ["", "## Removed duplicates", ""] + [f"- {name}" for name in s.duplicates]
    path.write_text("\n".join(lines) + "\n")
