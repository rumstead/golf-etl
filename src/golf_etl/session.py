"""Session naming and the session.md summary."""

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from golf_etl.ball import BallFlight, summary_line
from golf_etl.club import DEFAULT as DEFAULT_CLUB
from golf_etl.detect import Rejected, Swing
from golf_etl.frames import Timing
from golf_etl.measures import ANGLES, BODY_COLUMNS, by_grade
from golf_etl.sim import SimShot, signed_text, signed_value

_GUIDE_INTRO = """## How to read this session

- Each `swing-NN/` folder is one swing, in the order hit.
- Start with `06-sequence.jpg`: the whole swing in one image, read left to right, top row \
then bottom row: address, takeaway, halfway back, top, transition, impact, follow-through, finish. \
Takeaway is the hands reaching hip height going back (a fixed short time before the top \
when the hips are not in the pose), and halfway back is the hands reaching shoulder height, \
so those panels line up across swings. Transition and follow-through are the same short time \
before and after impact on every swing. Every panel of a swing is the same crop of the frame \
around the golfer, the club and the ball.
- `05-impact-zoom.jpg` is the ball area at full resolution for the frames just before, at, \
and after impact, top to bottom. Use it for contact and strike, and for shaft lean when the \
camera is face-on.
"""

_DRIVE_COPIES = """\
- `06-sequence-small.jpg`, `05-impact-zoom-small.jpg`, and `07-after-shot-small.jpg` are \
the same images under 72KB, small enough to read through the Drive connector. Use them when \
the full-size image does not open.
"""

_AFTER_SHOT = """\
- `07-after-shot.jpg` is a full-resolution frame about 13s after impact, or earlier when the \
next swing starts sooner. On a simulator \
or launch monitor its screen shows this shot's numbers (club path, face, speeds, spin, carry). \
Numbers visible in any other frame belong to the previous shot.
"""

_AFTER_SHOT_SIM = """\
- `07-after-shot.jpg` is a full-resolution frame about 13s after impact, or earlier when the \
next swing starts sooner. Its simulator screen shows this shot's numbers, which the Sim table \
below already has as text: look here for the cells the table leaves blank. Carry (game) and \
total length count up for about 13s after impact while the ball flies and rolls, so in an \
earlier frame they can still be short of the final values the table has. Numbers visible in \
any other frame belong to the previous shot.
- `sim.json` in each swing folder is that swing's Sim table row with each number's vote count \
and when it first showed, and `"unchanged": true` when the screen did not change after the swing.
"""

_DRIVE_CLIP = "- `clip.mp4` is the swing as video.\n"

_POSE = """\
- `pose.csv` is the pose estimate as numbers, one row per sample (up to 60 a second): time in \
the video, ms from impact, {frame}, then x, y and visibility for the nose, \
shoulders, elbows, wrists, hips, knees, and heels. x and y are fractions of the frame width \
and height from the top left; empty cells mean no pose. Use it to measure hip turn, head \
movement, and hand height instead of estimating from the images.
- Every image has its position and its time in the video in the top-left corner.
- The Timing table gives takeaway start (the club leaving the ball), top, and impact as \
{numbers} with their time in the video, plus backswing and downswing time and \
tempo (backswing over downswing). A takeaway start marked `hands` is the hands leaving \
address instead, because the club was not seen leaving the ball; down the line that reads \
late, so its backswing and tempo read short and do not compare with the other swings. When \
the hands are not seen at the top (out of frame) the top is not known and the durations are \
left blank.
"""

_VENUE = """\
- Venue is sim when the simulator's readout panel was found in the video, otherwise range. \
Camera setups differ between the two, so compare pose and panels only with sessions at the \
same venue. Club is the club the golfer picked when uploading; mixed is a session of several \
clubs, so its swings do not compare with each other.
"""


def guide(storage: str, sim: bool = False) -> str:
    """The reading guide for what the publisher leaves in the session folder; `sim` when the
    swings have a Sim table row and `sim.json`."""
    after_shot = _AFTER_SHOT_SIM if sim else _AFTER_SHOT
    if storage == "drive":
        return (
            _GUIDE_INTRO
            + _DRIVE_COPIES
            + after_shot
            + _DRIVE_CLIP
            + _POSE.format(frame="the nearest `clip.mp4` frame", numbers="`clip.mp4` frame numbers")
            + _VENUE
        )
    return (
        _GUIDE_INTRO
        + after_shot
        + _POSE.format(
            frame="the frame number in the swing",
            numbers="frame numbers counted from 2.5s before impact",
        )
        + _VENUE
    )


BALL_GUIDE = """\
- Ball px/frame, Start dir and Grade come from following the ball in the swing video. \
Ball px/frame is how fast it leaves the club, in pixels per video frame, so compare it only \
within this session. Grade compares it to the session median: good is near the median or \
faster, poor is well under it, fair is between; `ball.json` in each swing folder has the \
thresholds. \
On a sim session where enough swings have a smash read, Grade comes from the sim's smash \
factor against the session median instead (good near the median or above, poor well under it, \
with the thresholds in `ball.json`), a swing without a smash read has no grade, and the summary \
line says which basis was used. \
Start dir is sideways over up: negative starts left of the camera line, positive right. \
A `lost:` track says why the ball could not be followed; on the px basis that swing has no \
grade, and on the smash basis its grade still comes from the sim.
"""

BODY_GUIDE = """\
- The Body table measures the pose at the top and at impact against address, in leg lengths \
(hip to ankle), so it compares across swings at one venue. View is the camera angle, from \
which way the feet point. Down the line, side is toward the ball and the spine angle is the \
bend toward the ball in degrees. Face-on, side is to the right of the picture and the spine \
angle is the tilt that way. Up is positive. Hands top is how far the hands are over the \
shoulders at the top. A blank cell means the pose did not see the joint. The pose is an \
estimate, so read a measure across swings, not one value alone.
- The By grade table averages each number in this file over the swings of each grade. A \
number that is the same on good and poor swings does not explain the difference between them.
"""

INDEX_GUIDE = """\
One row per session, oldest first. Each number is the session's median over the swings that \
have it, from that session's `session.md`. Path is positive in-to-out, the faces are \
positive open, and side spin is negative to the left. Tempo is backswing over downswing. \
Compare sessions at the same venue. A row with only a venue is a session without a \
`summary.json`.
"""

SIM_GUIDE = """\
The numbers below are read off the simulator screen. A blank cell means the screen was covered \
or unreadable in most frames; check `07-after-shot.jpg` for those. A row of blanks can also \
mean the read failed, that the next swing started before this shot's numbers showed, or that \
the screen did not change after this swing (the simulator did not register a shot; `sim.json` \
has `"unchanged": true`). In the last two cases `07-after-shot.jpg` shows the previous shot.
Side spin, Launch (the launch angle up from the ground), Side launch (the launch angle to \
the side) and Offline come from a second panel on the left of the screen, which the golfer \
often stands in front of, so they are blank more often. They are as the simulator shows them: \
a negative number is to the left.
"""

SIM_COLUMNS = {
    "Club mph": "club_speed",
    "Ball mph": "ball_speed",
    "Smash": "smash",
    "Carry raw yd": "carry_raw",
    "Carry game yd": "carry_game",
    "Total yd": "total_length",
    "Peak ft": "peak_height",
    "Back spin": "back_spin",
    "Total spin": "total_spin",
    "Path": "club_path",
    "Face to path": "face_to_path",
    "Face to target": "face_to_target",
    "Side spin": "side_spin",
    "Launch": "vla",
    "Side launch": "hla",
    "Offline yd": "offline",
}


TIMING_COLUMNS = {"Backswing s": "backswing_s", "Downswing s": "downswing_s", "Tempo": "tempo"}
INDEX_COLUMNS = {
    "Club mph": "club_speed",
    "Ball mph": "ball_speed",
    "Smash": "smash",
    "Carry yd": "carry_raw",
    "Peak ft": "peak_height",
    "Path": "club_path",
    "Face to path": "face_to_path",
    "Face to target": "face_to_target",
    "Side spin": "side_spin",
    "Launch": "vla",
    "Tempo": "tempo",
}
SUMMARY = "summary.json"
INDEX = "index.md"


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
    # one per swing, None when its read failed; empty when the video shows no readout panel
    sims: list[SimShot | None] = field(default_factory=list)
    storage: str = "github"
    venue: str = "range"  # "sim" when the readout panel was found in the video
    grade_basis: str = "ball_px"  # "sim_smash" when the grades come from the sim's smash factor
    # one per swing, in order: `measures.body_measures` of the swing
    bodies: list[dict[str, float | str | None]] = field(default_factory=list)
    club: str = DEFAULT_CLUB  # "7 iron", "driver", or "mixed" for a session of several clubs


def video_time(t: float) -> str:
    """A time in the video, read like the Photos scrubber: seconds, then minutes past a minute."""
    if round(t, 2) < 60:
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
        f"- Venue: {s.venue}",
        f"- Club: {s.club}",
        "",
    ]
    if s.swings and s.balls:
        guide_text = guide(s.storage, bool(s.sims)) + (BODY_GUIDE if s.bodies else "")
        lines += [guide_text + BALL_GUIDE, summary_line(s.balls, s.ball_median, s.grade_basis), ""]
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
        lines += [guide(s.storage, bool(s.sims))]
        lines += ["| Swing | Impact | Strength | Hand speed peak |", "|---|---|---|---|"]
        for n, swing in enumerate(s.swings, 1):
            lines.append(
                f"| swing-{n:02d} | {video_time(swing.impact_s)} | {swing.strength:.2f} "
                f"| {swing.peak_offset_ms:+.0f}ms |"
            )
    else:
        lines.append("No swings detected.")
    if s.swings and s.sims:
        lines += ["", "## Sim", "", SIM_GUIDE]
        lines += [
            "| Swing | " + " | ".join(SIM_COLUMNS) + " |",
            "|---" * (len(SIM_COLUMNS) + 1) + "|",
        ]
        for n, shot in enumerate(s.sims, 1):
            values = shot.values if shot else {}
            cells = [f"swing-{n:02d}"] + [values.get(f) or "" for f in SIM_COLUMNS.values()]
            lines.append("|" + "|".join(f" {c} " if c else " " for c in cells) + "|")
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
            start += "" if t.start_from_club else " hands"
            impact = f"{t.impact_frame} ({video_time(t.impact_s)})"
            if t.top_in_frame:
                top = f"{t.top_frame} ({video_time(t.top_s)})"
                durations = f"{t.backswing_s:.2f}s | {t.downswing_s:.2f}s | {t.tempo:.1f}"
            else:
                top, durations = "hands not seen at the top", "| |"
            lines.append(f"| swing-{n:02d} | {start} | {top} | {impact} | {durations} |")
    if s.swings and s.bodies:
        lines += [
            "",
            "## Body",
            "",
            "| Swing | View | " + " | ".join(BODY_COLUMNS) + " |",
            "|---" * (len(BODY_COLUMNS) + 2) + "|",
        ]
        for n, body in enumerate(s.bodies, 1):
            cells = [f"swing-{n:02d}", body.get("view") or ""]
            cells += [_number(key, body.get(key)) for key in BODY_COLUMNS.values()]
            lines.append("|" + "|".join(f" {c} " if c else " " for c in cells) + "|")
        counts, means = by_grade([b.grade for b in s.balls], _numbers(s))
        if len(counts) > 1:
            lines += [
                "",
                "## By grade",
                "",
                "| Measure | " + " | ".join(f"{g} ({n})" for g, n in counts.items()) + " |",
                "|---" * (len(counts) + 1) + "|",
            ]
            for name, key in (SIM_COLUMNS | TIMING_COLUMNS | BODY_COLUMNS).items():
                cells = [_number(key, means[key][g]) for g in counts] if key in means else []
                if any(cells):
                    lines.append(f"| {name} | " + " | ".join(cells) + " |")
    if s.rejected:
        lines += ["", "## Rejected candidates", "", "| Time | Strength | Reason |", "|---|---|---|"]
        lines += [f"| {video_time(r.time_s)} | {r.strength:.2f} | {r.reason} |" for r in s.rejected]
    if s.duplicates:
        lines += ["", "## Removed duplicates", ""] + [f"- {name}" for name in s.duplicates]
    path.write_text("\n".join(lines) + "\n")


def session_summary(s: SessionSummary) -> dict:
    """The session's row for the index: its grade counts and the median of each number over
    the swings that have it."""
    medians = {k: round(statistics.median(v), 3) for k, v in _numbers(s, some=True).items()}
    return {
        "session": s.session_id,
        "source": s.source_name,
        "captured": s.captured.isoformat(),
        "venue": s.venue,
        "club": s.club,
        "swings": len(s.swings),
        "grades": {g: sum(b.grade == g for b in s.balls) for g in ("good", "fair", "poor")},
        "grade_basis": s.grade_basis,
        "medians": medians,
    }


def write_summary_json(path: Path, s: SessionSummary) -> None:
    path.write_text(json.dumps(session_summary(s), indent=2) + "\n")


def write_index(sessions: Path, marker: str) -> None:
    """Write index.md in the sessions folder from each session's summary.json. A session
    folder with only its `marker` file gets a row with its venue."""
    lines = ["# Sessions", "", INDEX_GUIDE]
    lines += [
        "| Session | Venue | Swings | Good | Fair | Poor | " + " | ".join(INDEX_COLUMNS) + " |",
        "|---" * (len(INDEX_COLUMNS) + 6) + "|",
    ]
    for folder in sorted(p for p in sessions.iterdir() if p.is_dir()):
        cells = [folder.name]
        if (folder / SUMMARY).exists():
            row = json.loads((folder / SUMMARY).read_text())
            grades, medians = row["grades"], row["medians"]
            cells += [row["venue"], str(row["swings"])]
            cells += [str(grades[g]) for g in ("good", "fair", "poor")]
            cells += [_number(key, medians.get(key)) for key in INDEX_COLUMNS.values()]
        elif (folder / marker).exists():
            cells += [json.loads((folder / marker).read_text()).get("venue", "")]
            cells += [""] * (len(INDEX_COLUMNS) + 4)
        else:
            continue
        lines.append("|" + "|".join(f" {c} " if c else " " for c in cells) + "|")
    (sessions / INDEX).write_text("\n".join(lines) + "\n")


def _numbers(s: SessionSummary, some: bool = False) -> dict[str, list[float | None]]:
    """Every sim, timing and body number by key, one value per swing in swing order. With
    `some`, only the values that are there, and only the keys that have any."""
    rows: dict[str, list[float | None]] = {}
    for key in SIM_COLUMNS.values():
        rows[key] = [signed_value(key, shot.values.get(key)) if shot else None for shot in s.sims]
    # a start timed off the hands reads late, so it stays out when the club timed any swing
    club = any(t.start_from_club for t in s.timings)
    timed = [t if t.top_in_frame and t.start_from_club == club else None for t in s.timings]
    for key in TIMING_COLUMNS.values():
        rows[key] = [getattr(t, key) if t else None for t in timed]
    for key in BODY_COLUMNS.values():
        rows[key] = [body.get(key) for body in s.bodies]
    if not some:
        return {k: v for k, v in rows.items() if v}
    found = {k: [x for x in v if x is not None] for k, v in rows.items()}
    return {k: v for k, v in found.items() if v}


def _number(key: str, value: float | None) -> str:
    """A number as session.md shows it for its key, empty for None."""
    if value is None:
        return ""
    if key in SIM_COLUMNS.values():
        return signed_text(key, value)
    if key in BODY_COLUMNS.values():
        return f"{value:.1f}" if key in ANGLES else f"{value:+.2f}"
    return f"{value:.1f}" if key == "tempo" else f"{value:.2f}"
