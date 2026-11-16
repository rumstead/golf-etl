"""chain.md: what moves with strike quality across every published swing of one club, and how
takeaway, posture and strike sit together, built from each session's summary.json and the
golfer's labels of the takeaway and halfway-back panels, without decoding video."""

import csv
import json
import statistics
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

from scipy.stats import spearmanr

from golf_etl.session import BODY_COLUMNS, SIM_COLUMNS, SUMMARY, TIMING_COLUMNS

CHAIN = "chain.md"
# the golfer's read of each takeaway (and halfway-back) panel: session, swing, label, side, by,
# date, and optionally position. The pipeline never writes it.
LABELS = "takeaway-labels.csv"
TAKEAWAYS = ("hanging", "level", "set", "unreadable")
TAKEAWAY, HALFWAY = "takeaway", "halfway"  # a label's position; a blank one is the takeaway
# Table 1's label rows: 1 for a level or set club at that position, 0 for a hanging one
LABEL_KEYS = {TAKEAWAY: "takeaway_set", HALFWAY: "halfway_set"}
# Table 2's takeaway rows: a label to its row, and the rows in order. Unreadable and unlabeled
# swings share the last row.
TAKEAWAY_ROWS = {"hanging": "hanging", "level": "level or set", "set": "level or set"}
NOT_LABELED = "not labeled"
CLUB = "7 iron"  # a summary.json without a club predates the club tag, when every session was one
MIXED = "mixed"
VIEW = "down the line"  # face-on body numbers measure other things, so they stay out
MIN_SESSION = 4  # a session needs this many values of a measure for its median to mean anything
MIN_READ = 10  # fewer swings than this behind a number and it is marked too few to read
MIN_GOOD = 2  # good swings a session needs for its strike to be anchored on them
RECENT_FROM = date(2026, 10, 31)  # the first session after the lessons started
SPINE_LOSS = "spine_loss"  # spine_address minus spine_impact: degrees stood up by impact
STRIKE = "strike"
BALL_SPEED = "ball_speed"
SWING_ORDER = "swing"  # the swing's number in its session: warm-up shows as a rise with it
# the measures the first table ranks, in its order: the takeaway and halfway labels, swing
# order, the posture link, strike, then the rest
NAMES = (
    {
        "Takeaway level or set": LABEL_KEYS[TAKEAWAY],
        "Halfway level or set": LABEL_KEYS[HALFWAY],
        "Swing order": SWING_ORDER,
        "Spine loss": SPINE_LOSS,
        "Hips impact side": "hips_impact_side",
        "Strike": STRIKE,
    }
    | BODY_COLUMNS
    | TIMING_COLUMNS
    | SIM_COLUMNS
)


@dataclass(frozen=True)
class Swing:
    session: str
    swing: int
    captured: date
    grade: str | None
    # each measure minus its session's median, for the measures the session has enough of, and
    # each position's label as 1 (level or set) or 0 (hanging) as it is
    relative: dict[str, float]
    # strike minus its session's anchor: the median strike of the good swings, or of every
    # swing without MIN_GOOD good ones. Larger is toward the toe.
    strike_off: float | None
    takeaway: str = NOT_LABELED  # its row in Table 2, from the golfer's label


def write_chain(sessions: Path) -> None:
    """Write chain.md in the sessions folder from every session's summary.json and the
    takeaway labels next to them."""
    labels, unused = read_labels(sessions / LABELS)
    by_club: dict[str, list[Swing]] = {}
    skipped: list[str] = []
    for folder in sorted(p for p in sessions.iterdir() if p.is_dir()):
        path = folder / SUMMARY
        if not path.exists():
            continue
        row = json.loads(path.read_text())
        club = row.get("club", CLUB)
        if club == MIXED:
            skipped.append(f"{folder.name} (mixed clubs)")
            continue
        if "per_swing" not in row:
            skipped.append(f"{folder.name} (published before per-swing numbers)")
            continue
        swings = _relative(folder.name, row)
        by_club.setdefault(club, []).extend(_labeled(s, labels) for s in swings)
    (sessions / CHAIN).write_text(_render(by_club, skipped, unused))


def _labeled(s: Swing, labels: dict[tuple[str, int, str], str]) -> Swing:
    """The swing with its takeaway row and its label rows for Table 1."""
    relative = dict(s.relative)
    for position, key in LABEL_KEYS.items():
        label = labels.get((s.session, s.swing, position))
        if label in TAKEAWAY_ROWS:
            relative[key] = 0.0 if label == "hanging" else 1.0
    takeaway = TAKEAWAY_ROWS.get(labels.get((s.session, s.swing, TAKEAWAY), ""), NOT_LABELED)
    return replace(s, relative=relative, takeaway=takeaway)


def read_labels(path: Path) -> tuple[dict[tuple[str, int, str], str], list[str]]:
    """Each label by (session, swing number, position), and the rows that could not be used,
    as `line N (why)`. A later row for the same swing and position wins."""
    if not path.exists():
        return {}, []
    labels: dict[tuple[str, int, str], str] = {}
    unused = []
    with path.open(newline="") as fh:
        for n, row in enumerate(csv.DictReader(fh), 2):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            label = row.get("label", "").lower()
            try:
                swing = int(row.get("swing", "").removeprefix("swing-"))
            except ValueError:
                unused.append(f"line {n} (swing {row.get('swing')!r})")
                continue
            if label not in TAKEAWAYS:
                unused.append(f"line {n} (label {row.get('label')!r})")
                continue
            position = row.get("position", "").lower() or TAKEAWAY
            if position not in LABEL_KEYS:
                unused.append(f"line {n} (position {row.get('position')!r})")
                continue
            labels[(row.get("session", ""), swing, position)] = label
    return labels, unused


def _relative(session: str, row: dict) -> list[Swing]:
    """The session's down-the-line swings with each measure taken from its session median."""
    swings = [s for s in row["per_swing"] if s.get("view") == VIEW]
    for s in swings:
        if s.get("spine_address") is not None and s.get("spine_impact") is not None:
            s[SPINE_LOSS] = s["spine_address"] - s["spine_impact"]
    keys = {k for s in swings for k, v in s.items() if isinstance(v, int | float)}
    medians = {}
    for key in keys:
        values = [s[key] for s in swings if s.get(key) is not None]
        if len(values) >= MIN_SESSION:
            medians[key] = statistics.median(values)
    good = [s[STRIKE] for s in swings if s.get("grade") == "good" and s.get(STRIKE) is not None]
    anchor = statistics.median(good) if len(good) >= MIN_GOOD else medians.get(STRIKE)
    captured = datetime.fromisoformat(row["captured"]).date()
    out = []
    for s in swings:
        relative = {k: s[k] - m for k, m in medians.items() if s.get(k) is not None}
        off = None if anchor is None or s.get(STRIKE) is None else s[STRIKE] - anchor
        out.append(Swing(session, s["swing"], captured, s.get("grade"), relative, off))
    return out


def _render(by_club: dict[str, list[Swing]], skipped: list[str], unused: list[str]) -> str:
    lines = ["# Swing chain", "", GUIDE]
    if skipped:
        lines += ["Left out: " + ", ".join(skipped) + ".", ""]
    if unused:
        lines += [f"Rows of {LABELS} not used: " + ", ".join(unused) + ".", ""]
    if not by_club:
        lines.append("No session has per-swing numbers yet.")
    for club, swings in sorted(by_club.items()):
        recent = [s for s in swings if s.captured >= RECENT_FROM]
        for title, group in ((club, swings), (f"{club}, from {RECENT_FROM:%Y-%m-%d} on", recent)):
            sessions = len({s.session for s in group})
            plural = "" if sessions == 1 else "s"
            count = f"{len(group)} swings from {sessions} session{plural}."
            lines += ["", f"## {title}", "", count, ""]
            lines += _moves_with_strike(group) + [""] + _chain_counts(group)
    return "\n".join(lines) + "\n"


def _moves_with_strike(swings: list[Swing]) -> list[str]:
    """Table 1: each measure's rank correlation with ball speed and with strike."""
    lines = [
        "| Measure | With ball speed | Swings | With strike | Swings | Sessions |",
        "|---|---|---|---|---|---|",
    ]
    for name, key in NAMES.items():
        mine = [s for s in swings if key in s.relative]
        if not mine:
            continue
        speed, n_speed = _rho(mine, key, BALL_SPEED) if key != BALL_SPEED else ("", 0)
        strike, n_strike = _rho(mine, key, STRIKE) if key != STRIKE else ("", 0)
        sessions = len({s.session for s in mine})
        cells = [name, speed, _count(n_speed), strike, _count(n_strike), str(sessions)]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _chain_counts(swings: list[Swing]) -> list[str]:
    """Table 2: swings counted by takeaway, posture kept or lost, and strike near the anchor or
    off it."""
    rows = [s for s in swings if SPINE_LOSS in s.relative and s.strike_off is not None]
    if not rows:
        return ["Takeaway, posture and strike: no swing has both a posture and a strike."]
    lost_cut = statistics.median(s.relative[SPINE_LOSS] for s in rows)
    off_cut = statistics.median(abs(s.strike_off) for s in rows)
    lines = [
        f"Posture is lost when the spine stood up more than {lost_cut:+.1f} degrees against "
        f"its session's median, and strike is off when it is more than {off_cut:.2f} ball "
        "diameters from its session's good swings (or from its session's median, in a "
        "session with fewer than two good swings). Both cuts are the medians of these swings. "
        "Mean strike off is signed, positive toward the toe.",
        "",
        "| Takeaway | Posture | Swings | Strike near | Strike off | Mean strike off |",
        "|---|---|---|---|---|---|",
    ]
    for takeaway in ("hanging", "level or set", NOT_LABELED):
        mine = [s for s in rows if s.takeaway == takeaway]
        if not mine:
            continue
        for label, lost in (("kept", False), ("lost", True)):
            group = [s for s in mine if (s.relative[SPINE_LOSS] > lost_cut) == lost]
            near = sum(abs(s.strike_off) <= off_cut for s in group)
            mean = statistics.mean(s.strike_off for s in group) if group else None
            cells = [
                takeaway,
                label,
                _count(len(group)),
                _count(near),
                _count(len(group) - near),
                "" if mean is None else f"{mean:+.2f}",
            ]
            lines.append("| " + " | ".join(cells) + " |")
    return lines


def _rho(swings: list[Swing], key: str, other: str) -> tuple[str, int]:
    pairs = [(s.relative[key], s.relative[other]) for s in swings if other in s.relative]
    if len(pairs) < 3:
        return "", len(pairs)
    a, b = zip(*pairs, strict=True)
    if len(set(a)) < 2 or len(set(b)) < 2:
        return "", len(pairs)
    return f"{spearmanr(a, b).statistic:+.2f}", len(pairs)


def _count(n: int) -> str:
    if n == 0:
        return ""
    return f"{n} (too few)" if n < MIN_READ else str(n)


GUIDE = f"""\
Every published swing of one club, down the line, pooled across sessions. Each number is the \
swing's value minus its session's median, so where the phone stood and the day drop out; a \
session needs {MIN_SESSION} values of a measure for it to count. Mixed-club sessions are left \
out.

The first table ranks each measure against ball speed (sim sessions only) and against strike \
(larger is toward the toe). A rank correlation near +1 or -1 means the measure moves with it \
across swings; near 0 means it does not. It shows what travels together, not what causes \
what. Takeaway and Halfway level or set are the golfer's labels, 1 for a level or set club and \
0 for a hanging one, taken as they are. Swing order is the swing's place in its session: when \
ball speed climbs with it, warm-up can pass for anything else that changed during the session. \
Spine loss is degrees stood up from address to impact. It is partly the shoulders turning, so \
read it beside Hips impact side.

The second table counts swings by takeaway, posture and strike. The takeaway is the golfer's \
label for the swing's takeaway panel in `{LABELS}` (columns session, swing, label, side, by, \
date, and position, `takeaway` when blank or `halfway` for the halfway-back panel): hanging \
when the clubhead is clearly below the hands, level or set otherwise. A swing \
labeled unreadable, or not labeled, is counted in its own rows. A count under {MIN_READ} is \
marked too few to read.

Each club then repeats both tables for sessions from {RECENT_FROM:%Y-%m-%d} on, when the \
lessons started.
"""
