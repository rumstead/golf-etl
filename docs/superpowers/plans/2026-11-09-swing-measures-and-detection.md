# Swing Measures and Screen-Change Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tag each session's venue, grade sim days by smash factor, take the takeaway panel when the hands reach the hips, write the elbows to `pose.csv`, keep every swing that changed the sim screen, and record a shaft angle spike.

**Architecture:** Five small changes to the existing modules, one PR each. The panel search moves ahead of the detection dedupe and the dedupe takes a screen-change callback, so `detect.py` stays free of OpenCV and the sim module. The `Panel` type moves to a tiny `panel.py` both can import. Grading gains a second basis chosen per session.

**Tech Stack:** Python 3.12, numpy, OpenCV, MediaPipe, pytest. Tests run in the `golf-etl:test` container.

**Spec:** `docs/superpowers/specs/2026-11-09-swing-measures-and-detection-design.md`

## Global Constraints

- Blank with a reason beats a wrong number, in every output.
- TDD: write the failing test, watch it fail, make it pass, run the file, run the suite before the PR.
- Unit tests: `podman run --rm -v "$PWD":/repo:Z -e PYTHONPATH=/repo/src -w /repo golf-etl:test python -m pytest <path> -q`. Real-footage tests (`-m real`) read `local/`, which is mounted with the repo, and take minutes per video; run the ones named in the task, not the whole marker, until the PR step.
- Lint: `podman run --rm -v "$PWD":/repo:Z -w /repo golf-etl:test sh -c "ruff check . && ruff format --check ."`
- Each task is its own branch off `main` and its own PR. If the previous PR is not merged yet, branch off it and rebase after the merge. Commits: `<prefix>(<scope>): <imperative lowercase>` with `git commit -s`, no body, no attribution. Prefixes in use: `feat`, `fix`, `test`, `docs`, `chore`.
- All text that lands in the repo (code comments, test names, docs, commit and PR text) follows the `writing-as-rumstead` skill. No em or en dashes anywhere.
- `docs/design.md` is updated in the same PR to say what is true after it, never how it got there.
- Regression floors that every PR keeps: `test_detection_matches_the_labels` at precision and recall 1.0; `test_sim_reads_held_out_videos_without_a_wrong_number` and `test_committed_sim_glyphs_read_labeled_fields_with_no_wrong_number` passing; the full unit suite green.

## Review Focus

- A session with zero swings still gets a Venue line, and it says `range`, since no panel search ran. Test in Task 1.
- A sim session with fewer than 3 smash reads grades by ball px/frame and says so, so a half-read session does not silently change basis. Test in Task 2.
- The hands reach the hip line before the takeaway start on a jittery address: the takeaway is never before the takeaway start. Test in Task 3.
- The screen-change read raises (a decode error mid-session): detection falls back to the hand-speed rule for that span instead of failing the video. Test in Task 4.
- A clip under 15 s with a sim screen keeps today's single-swing rule and never calls the screen check. Test in Task 4.

---

### Task 1: Venue tag

**Files:**
- Modify: `src/golf_etl/session.py` (SessionSummary, write_session_md, guide)
- Modify: `src/golf_etl/pipeline.py` (SessionResult, process_video)
- Modify: `src/golf_etl/storage/publish.py:21-27` (marker), `src/golf_etl/storage/poller.py:112` (marker call)
- Modify: `docs/design.md` (Identity and idempotency marker line; the `session.md` paragraph under Slicing and frames)
- Test: `tests/test_session.py`, `tests/test_pipeline.py`, the test that asserts the marker keys (`grep -rn pipelineVersion tests/`)

**Interfaces:**
- Produces: `SessionSummary.venue: str = "range"`; `SessionResult.venue: str`; `marker(checksum: str, source_name: str, version: str, venue: str) -> dict` with a `"venue"` key.

- [ ] **Step 0: Branch and commit the design and this plan**

```bash
git checkout main && git pull -q && git checkout -b feat/venue-tag
git add docs/superpowers/specs/2026-11-09-swing-measures-and-detection-design.md docs/superpowers/plans/2026-11-09-swing-measures-and-detection.md
git commit -s -m "docs: design and plan for swing measures and screen-change detection"
```

- [ ] **Step 1: Write the failing session tests**

In `tests/test_session.py`, using the existing `summary(**kw)` helper:

```python
def test_session_md_names_the_venue(tmp_path):
    write_session_md(tmp_path / "session.md", summary(venue="sim"))
    lines = (tmp_path / "session.md").read_text().splitlines()
    assert lines.index("- Venue: sim") == lines.index("- Swings: 2") + 1


def test_session_md_with_no_swings_is_a_range_session(tmp_path):
    write_session_md(tmp_path / "session.md", summary(swings=[], balls=[]))
    assert "- Venue: range" in (tmp_path / "session.md").read_text()


def test_the_reading_guide_explains_the_venue(tmp_path):
    write_session_md(tmp_path / "session.md", summary(venue="sim"))
    text = (tmp_path / "session.md").read_text()
    assert "Venue is sim when the simulator's readout panel was found in the video" in text
```

Check `summary()` builds two swings; if not, match the Swings count it produces.

- [ ] **Step 2: Run them to verify they fail**

Run: `... python -m pytest tests/test_session.py -q -k venue`
Expected: FAIL, `TypeError: unexpected keyword argument 'venue'` and the missing line.

- [ ] **Step 3: Implement in `session.py`**

Add `venue: str = "range"` to `SessionSummary`. In `write_session_md` write `f"- Venue: {s.venue}"` right after the Swings line. Add this bullet to the guide, after the `_POSE` block in `guide()`, for both storage modes:

```
- Venue is sim when the simulator's readout panel was found in the video, otherwise range. \
Camera setups differ between the two, so compare pose, panels and ball px/frame only with \
sessions at the same venue.
```

- [ ] **Step 4: Run the session tests**

Run: `... python -m pytest tests/test_session.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing pipeline and marker tests**

In `tests/test_pipeline.py`, next to `test_a_video_with_a_screen_writes_sim_json`:

```python
def test_a_video_with_a_screen_is_a_sim_session(tmp_path, screen_video):
    result = _process(screen_video, tmp_path / "out", [1.0])
    assert result.venue == "sim"
    assert "- Venue: sim" in (result.session_dir / "session.md").read_text()


def test_a_video_without_a_screen_is_a_range_session(tmp_path):
    video = make_video_with_clicks(tmp_path / "range.mp4", [1.0], seconds=8.0, size="640x360")
    result = _process(video, tmp_path / "out", [1.0])
    assert result.venue == "range"
```

In the marker test found by the grep, assert `marker("abc", "IMG.mov", "v1", "sim")["venue"] == "sim"` and that the poller's published `.golf-etl.json` carries `"venue"` (the poller tests use a fake processor returning `SessionResult`; give it `venue="range"`).

- [ ] **Step 6: Run them to verify they fail**

Run: `... python -m pytest tests/test_pipeline.py -q -k venue` and the marker test file.
Expected: FAIL on the missing attribute and key.

- [ ] **Step 7: Implement the plumbing**

`SessionResult` gains `venue: str`. In `process_video` set `venue = "sim" if panel is not None else "range"`, pass it to `SessionSummary` and `SessionResult`. `marker(checksum, source_name, version, venue)` adds `"venue": venue`; `poller.handle` passes `result.venue`. Fix every `SessionResult(` and `marker(` construction the tests and code have (`grep -rn "SessionResult(\|marker(" src tests`).

- [ ] **Step 8: Run the touched test files, then ruff**

Run: `... python -m pytest tests/test_pipeline.py tests/test_session.py tests/test_poller.py tests/test_github_publisher.py -q`
Expected: PASS.

- [ ] **Step 9: Update `docs/design.md`**

Marker line becomes `{sha256, sourceName, pipelineVersion, published, venue}`. In the `session.md` paragraph, after "pipeline version", add "the venue (`sim` when the readout panel was found, otherwise `range`)".

- [ ] **Step 10: Commit, full suite, PR**

```bash
git add -A src tests docs/design.md
git commit -s -m "feat(session): tag each session with its venue"
```

Run the full suite in the container (background, it takes over an hour with the real tests), then `git push -u origin feat/venue-tag` and `gh pr create` with a two-sentence description: what the tag is and when it says sim.

---

### Task 2: Grade sim sessions by smash factor

**Files:**
- Modify: `src/golf_etl/ball.py:227-278` (grade, summary_line, write_ball_json)
- Modify: `src/golf_etl/config.py` (three settings), `src/golf_etl/pipeline.py:128-135`, `src/golf_etl/session.py` (SessionSummary.grade_basis, BALL_GUIDE, summary line call)
- Modify: `docs/design.md` (Ball flight step 5; Requirements under Swing frames)
- Test: `tests/test_ball_grade.py`, `tests/test_session.py`, `tests/test_real.py`

**Interfaces:**
- Produces in `ball.py`:
  - `grade_by_smash(flights: list[BallFlight], smashes: list[float | None], good: float, poor: float) -> tuple[list[BallFlight], float | None]`: one smash per flight, None when unread; the median is over the read smashes; a flight with no smash gets `grade=None` whatever its track.
  - `grade_session(flights: list[BallFlight], smashes: list[float | None], cfg: Settings) -> tuple[list[BallFlight], float | None, str]`: basis `"sim_smash"` when `sum(s is not None for s in smashes) >= cfg.smash_min_reads`, else `"ball_px"` via today's `grade`.
  - `summary_line(flights, median, basis: str = "ball_px") -> str`.
  - `write_ball_json(path, flight, *, median, good, poor, basis: str = "ball_px")`: `params["basis"]`.
  - `smash_value(shot: SimShot | None) -> float | None` in `sim.py`: `float(shot.values["smash"])` when read.
- Settings: `smash_good: float = 0.97`, `smash_poor: float = 0.92`, `smash_min_reads: int = 3`.
- `SessionSummary.grade_basis: str = "ball_px"`.

- [ ] **Step 1: Write the failing grade tests**

In `tests/test_ball_grade.py`:

```python
SMASHES_4859 = [1.08, 1.29, 1.27, 1.35, 1.15, 1.37, 1.41, 1.39, 1.33, 1.42, 1.36, 1.37]


def test_smash_grades_against_the_session_median():
    flights = [ok(10.0)] * len(SMASHES_4859)
    graded, median = grade_by_smash(flights, SMASHES_4859, good=0.97, poor=0.92)
    assert median == pytest.approx(1.355)
    grades = [f.grade for f in graded]
    assert grades[0] == "poor" and grades[4] == "poor"  # the owner's two poor strikes
    assert grades[1:3] == ["fair", "fair"]
    assert grades[5:] == ["good"] * 7


def test_a_swing_without_a_smash_read_has_no_grade_even_when_tracked():
    graded, _ = grade_by_smash([ok(10.0), ok(12.0), ok(11.0)], [1.3, None, 1.4], 0.97, 0.92)
    assert [f.grade for f in graded] == ["fair", None, "good"]


def test_a_lost_track_with_a_smash_read_is_graded():
    graded, _ = grade_by_smash([BallFlight("lost: ball lost after launch"), ok(1.0)], [1.4, 1.3], 0.97, 0.92)
    assert graded[0].grade == "good"


def test_fewer_than_three_smash_reads_keeps_px_grading():
    flights = [ok(10.0), ok(20.0), ok(18.5)]
    graded, median, basis = grade_session(flights, [1.3, None, 1.4], Settings())
    assert basis == "ball_px" and median == pytest.approx(18.5)
    assert [f.grade for f in graded] == ["poor", "good", "good"]


def test_three_smash_reads_switch_the_session_to_smash():
    graded, median, basis = grade_session([ok(10.0)] * 3, [1.3, 1.4, 1.2], Settings())
    assert basis == "sim_smash" and median == pytest.approx(1.3)


def test_summary_line_names_the_smash_basis():
    graded, median = grade_by_smash([ok(1.0)] * 4, [1.3, 1.4, None, 1.2], 0.97, 0.92)
    assert summary_line(graded, median, "sim_smash") == (
        "Ball flight: 1 good, 1 fair, 1 poor, 1 ungraded (graded by sim smash factor, median 1.30)"
    )


def test_ball_json_names_its_basis(tmp_path):
    write_ball_json(tmp_path / "ball.json", ok(1.0), median=1.3, good=0.97, poor=0.92, basis="sim_smash")
    assert json.loads((tmp_path / "ball.json").read_text())["params"]["basis"] == "sim_smash"
```

Work out the grades in the fourth and seventh tests from the thresholds before running; adjust the literal expectations only if the arithmetic says so, never the thresholds.

- [ ] **Step 2: Run to verify they fail**

Run: `... python -m pytest tests/test_ball_grade.py -q`
Expected: FAIL with `ImportError` on the new names.

- [ ] **Step 3: Implement in `ball.py` and `config.py`**

Add the three settings with a comment like the `ball_good` one. Implement the three functions per the Interfaces block; `grade_by_smash` labels with the same `good * median` and `poor * median` rule as `grade`. `summary_line` with basis `"sim_smash"` counts `ungraded = sum(f.grade is None for f in flights)` and ends with `(graded by sim smash factor, median {median:.2f})`; the `"ball_px"` wording stays byte for byte.

- [ ] **Step 4: Run the grade tests**

Run: `... python -m pytest tests/test_ball_grade.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing session and sim tests**

`tests/test_session.py`: `test_session_md_summary_line_follows_the_grade_basis` builds `summary(grade_basis="sim_smash", ball_median=1.35)` with balls graded and asserts the summary line contains "graded by sim smash factor". `test_the_reading_guide_explains_smash_grading` asserts the text "at least 3 swings with a smash read" is in the guide.

`tests/test_sim.py`: `test_smash_value_parses_a_read_and_is_none_for_a_blank_or_missing_shot` with a `SimShot` whose `values["smash"]` is `"1.42"`, one with `None`, and `None` itself.

- [ ] **Step 6: Run to verify they fail, then implement**

`SessionSummary.grade_basis`; `write_session_md` calls `summary_line(s.balls, s.ball_median, s.grade_basis)`. Add to `BALL_GUIDE`:

```
On a sim session with at least 3 swings with a smash read, Grade comes from the sim's smash \
factor against the session median instead (good at 0.97 times the median or more, poor under \
0.92), a swing without a smash read has no grade, and the summary line says which basis was used. \
```

`smash_value` in `sim.py`. In `process_video`, replace the `grade(...)` call with `balls, ball_median, basis = grade_session(balls, [smash_value(s) for s in sims] if sims else [None] * len(balls), cfg)`, pass `basis` to `write_ball_json` and `grade_basis=basis` to `SessionSummary`.

- [ ] **Step 7: Run the unit files and ruff**

Run: `... python -m pytest tests/test_ball_grade.py tests/test_session.py tests/test_sim.py tests/test_pipeline.py -q`
Expected: PASS.

- [ ] **Step 8: Write the real-footage test**

In `tests/test_real.py`, add a module-scoped fixture `session_4859(tmp_path_factory)` that runs `process_video` on `LOCAL / "IMG_4859.mov"` once (skip when the file is missing) and returns the result. Then:

```python
@needs_sim_labels
def test_the_sim_session_grades_by_smash(session_4859):
    text = (session_4859.session_dir / "session.md").read_text()
    assert "graded by sim smash factor" in text
    rows = {r.split(" | ")[0].removeprefix("| "): r for r in text.splitlines() if r.startswith("| swing-")}
    grade = lambda n: rows[f"swing-{n:02d}"].split(" | ")[6]
    assert grade(1) == "poor" and grade(8) == "poor"
    assert all(grade(n) == "good" for n in (9, 10, 11, 12, 13, 14, 15))
    assert all(grade(n) == "" for n in (2, 5, 6))
```

Run: `... python -m pytest tests/test_real.py -q -k smash -s` (about 10 minutes).
Expected: PASS. If a last-seven swing grades fair, report the smash values and the median in the PR and stop: the thresholds are the owner's to move.

- [ ] **Step 9: Update `docs/design.md`**

Ball flight step 5 gains: "On a sim session with at least 3 swings whose smash factor was read (`GOLF_SMASH_MIN_READS`), every grade comes from smash against the session median instead: good at `GOLF_SMASH_GOOD` (0.97) times the median or more, poor under `GOLF_SMASH_POOR` (0.92). A swing without a smash read has no grade, `ball.json` names the basis, and the summary line says which one was used." The on-session result line: "On the 2026-11-08 sim session the two strikes the owner called poor graded poor and the last seven graded good."

- [ ] **Step 10: Commit, suite, PR**

```bash
git commit -s -m "feat(ball): grade sim sessions by smash factor"
```

PR text: the basis rule, the thresholds, and the IMG_4859 grades before and after (swings 11 and 15 were poor at 111 and 112 mph).

---

### Task 3: Takeaway at hip height and elbows in `pose.csv`

**Files:**
- Modify: `src/golf_etl/frames.py:106-114`, `src/golf_etl/pose.py:12-33`, `src/golf_etl/session.py` (`_GUIDE_INTRO`, `_POSE`)
- Modify: `docs/design.md` (sequence takeaway bullet; `pose.csv` columns bullet)
- Test: `tests/test_frames.py`, `tests/test_pose_track.py`, `tests/test_session.py`, `tests/test_real.py`

**Interfaces:**
- `pick_positions` keeps its signature; `takeaway_s` becomes the hands reaching the hip line, with `top_s - takeaway_before_top_s` as the fallback. `Positions.takeaway_s` semantics change; no field is added.
- `pose.py`: `LEFT_ELBOW, RIGHT_ELBOW = 13, 14`; `CSV_JOINTS` order: nose, left_shoulder, right_shoulder, left_elbow, right_elbow, left_wrist, right_wrist, hips, knees, heels.

- [ ] **Step 1: Rewrite the takeaway tests in `tests/test_frames.py`**

Replace `test_takeaway_is_a_fixed_time_before_the_top` with:

```python
def test_takeaway_is_when_the_hands_reach_the_hips():
    p = pick_positions(swing_track(10.0), 10.0, still_speed=0.15)
    assert p.takeaway_s == pytest.approx(8.9, abs=1 / 60)  # hips 0.1s after the takeaway start


def test_takeaway_falls_back_to_a_fixed_time_before_the_top_without_hips():
    track = swing_track(10.0)
    track.landmarks[:, [LEFT_HIP, RIGHT_HIP]] = np.nan
    p = pick_positions(track, 10.0, still_speed=0.15)
    assert p.takeaway_s == pytest.approx(p.top_s - 0.33)
```

Change `test_takeaway_is_never_before_the_takeaway_start` to assert `p.takeaway_s >= p.takeaway_start_s` and add a case where the hip line is reached while the hands are still within the address jitter: lower the hips to the hand height so the raised line is 0.1 leg lengths above address, and assert `p.takeaway_s >= p.takeaway_start_s` still holds. In `test_takeaway_is_well_into_the_backswing_when_the_hands_address_at_hip_height`, the takeaway is now the time the hands rise 0.1 leg lengths above address: compute it from `_hand_y` (0.5 frame heights per second going back, legs 0.3, so 0.06 s after 8.8) and assert `pytest.approx(8.86, abs=0.05)`.

- [ ] **Step 2: Run to verify they fail**

Run: `... python -m pytest tests/test_frames.py -q -k takeaway`
Expected: the two new tests FAIL on 9.27 versus 8.9 and so on; the fallback test passes already (hips missing already falls back), which is fine for a test that pins behavior being kept.

- [ ] **Step 3: Implement in `frames.py`**

In `pick_positions`, replace the `takeaway_s = max(top_s - takeaway_before_top_s, takeaway_start_s)` line with: `takeaway_s = max(at_hips, takeaway_start_s) if at_hips is not None else max(top_s - takeaway_before_top_s, takeaway_start_s)`. Update the docstring to say the takeaway is the hands reaching the hip line (raised `TAKEAWAY_RISE_LEGS` above address for hands that address at the hips) and names the fallback.

- [ ] **Step 4: Run the frames tests**

Run: `... python -m pytest tests/test_frames.py -q`
Expected: PASS.

- [ ] **Step 5: Elbows, test first**

In `tests/test_pose_track.py::test_pose_csv_has_one_row_per_sample_and_the_named_joints`, add `"left_elbow"` and `"right_elbow"` to the joint tuple and assert column order:

```python
    header = list(rows[0])
    assert header.index("right_shoulder_vis") < header.index("left_elbow_x") < header.index("left_wrist_x")
```

Run it, see it fail, then add `LEFT_ELBOW, RIGHT_ELBOW = 13, 14` and the two `CSV_JOINTS` entries between `right_shoulder` and `left_wrist`. Run again: PASS.

- [ ] **Step 6: Guide text, test first**

`tests/test_session.py`: `test_the_reading_guide_describes_the_takeaway_at_hip_height` asserts "Takeaway is the hands reaching hip height going back" is in the guide, and `test_the_reading_guide_lists_the_elbows` asserts "shoulders, elbows, wrists, hips, knees, and heels". Then edit `_GUIDE_INTRO` to:

```
Takeaway is the hands reaching hip height going back (a fixed short time before the top when \
the hips are not in the pose), and halfway back is the hands reaching shoulder height, so \
those panels line up across swings.
```

and `_POSE` to list "nose, shoulders, elbows, wrists, hips, knees, and heels". Run `tests/test_session.py`: PASS.

- [ ] **Step 7: Real-footage test**

In `tests/test_real.py`:

```python
@needs_sim_labels
def test_takeaway_hands_are_at_the_hips_on_the_sim_session():
    video = LOCAL / "IMG_4859.mov"
    cfg = Settings()
    detection = detect_video(video, probe(video), cfg)
    heights = []
    for swing in detection.swings:
        p = pick_positions(swing.track, swing.impact_s, cfg.still_speed, cfg.transition_s, cfg.follow_through_s, cfg.takeaway_before_top_s)
        i = int(np.argmin(np.abs(swing.track.times - p.takeaway_s)))
        heights.append(float(swing.track.hands_below_hips()[i]) / swing.track.leg_length())
    print(heights)
    assert all(-0.15 <= h <= 0.05 for h in heights)  # at the hip line, never well past it
```

Run: `... python -m pytest tests/test_real.py -q -k takeaway -s` (about 4 minutes). Expected: PASS. If a swing lands outside the band, print which and why (hips not in the pose means the fallback fired, which the band must then exclude by checking `np.isnan`) and narrow the assertion to swings with hips in the pose.

- [ ] **Step 8: `docs/design.md`**

Sequence bullet: "Takeaway is the first moment going back that the hands reach the hip line (raised 0.1 leg lengths above the address hand height, so a golfer whose hands address at the hips still gets a takeaway off address), which lines it up with halfway back across swings. Without hips in the pose it is `GOLF_TAKEAWAY_BEFORE_TOP_S` (default 0.33s) before the top and never before the takeaway start." Drop the sentence about the club being parallel 0.31 to 0.37s before the top, since that described the old rule. `pose.csv` bullet: add elbows to the joint list.

- [ ] **Step 9: Commit, suite, PR**

Two commits on one branch: `feat(frames): take the takeaway panel when the hands reach the hips` and `feat(pose): write the elbows to pose.csv`. PR text: why the panels now match across swings, and the hand-height band the real test enforces.

---

### Task 4: Keep every swing that changed the sim screen

**Files:**
- Create: `src/golf_etl/panel.py` (`Box`, `Panel` moved from `sim.py`; `sim.py` imports them from here)
- Modify: `src/golf_etl/detect.py`, `src/golf_etl/sim.py` (`screen_changed`), `src/golf_etl/pipeline.py` (`detect_video`, `_find_panel` reuse, `_screen_change_check`, `process_video`)
- Modify: `docs/design.md` (Swing detection step 5; Sim readout "Finding the panel"; Requirements "Louder sound nearby")
- Test: `tests/test_detect.py`, `tests/test_sim.py`, `tests/test_pipeline.py`, `tests/test_real.py`

**Interfaces:**
- `detect.py`:
  - `ScreenChanged = Callable[[float], bool | None]`: given a confirmed candidate's time, True when the screen changed after it, False when it did not, None when it cannot be read. Called in time order, only for candidates in a span of two or more.
  - `Detection.panel: Panel | None = None` (from `golf_etl.panel`).
  - `confirm_candidates(onsets, track_for, cfg) -> tuple[list[tuple[Onset, Check]], list[Rejected]]`.
  - `keep_swings(confirmed, rejected, track_for, duration_s, cfg, screen_changed: ScreenChanged | None = None) -> Detection`.
  - `detect_swings(onsets, track_for, duration_s, cfg, screen_changed=None) -> Detection` composes the two; existing callers unchanged.
  - Rejection reasons: `"within 8s of a swing that changed the screen"`, `"no screen change, within 8s of a faster swing"`, and today's `"within 8s of a faster swing"` when the screen could not be read. Clips under `short_clip_s` keep today's rule and never call the callback.
- `sim.py`: `screen_changed(before: dict[str, list[tuple[float, str | None]]], values: dict[str, str | None]) -> bool | None`: None when fewer than `MIN_COMPARED` fields compare, else `equal * 2 <= compared`.
- `pipeline.py`: `_screen_change_check(video, info, panel, cfg, times: list[float]) -> ScreenChanged`: a closure over the candidate times (for each candidate's before-window start) that reads before frames with `before_window(t, previous_t)`, after frames from `t + START_AFTER_S` to `min(t + 5.0, info.duration_s)` at `SAMPLE_FPS`, votes the after frames with `read_shot(frames, panel, glyphs)`, returns `screen_changed(before_reads, values)` and False when `values` match the last credited after-values (`compare_before`-style, at least `MIN_COMPARED` compared and more than half equal). On any exception it logs and returns None. `detect_video` runs confirm, then `_find_panel` on the confirmed candidates' times, then `keep_swings` with the check when a panel was found, and sets `Detection.panel`. `process_video` uses `detection.panel` and only searches itself when it is None (fake detectors in tests).

- [ ] **Step 1: Move `Panel` and `Box` to `panel.py`**

Create the module with the two definitions and their comments, import them in `sim.py` (keep the names exported from `sim` so nothing else changes). Run `tests/test_sim.py`: PASS. Commit `chore(sim): move the panel box type to its own module`.

- [ ] **Step 2: Write the failing detection tests**

In `tests/test_detect.py`:

```python
def test_keeps_the_swing_that_changed_the_screen_over_faster_hands():
    track_for = windows(session_track([10.0, 15.0], 30, down_s={15.0: 0.25}))
    changed = {10.0: True, 15.0: False}
    result = detect_swings([Onset(10.0, 0.9), Onset(15.0, 0.4)], track_for, 600, CFG, changed.get)
    assert [s.impact_s for s in result.swings] == [10.0]
    assert [(r.time_s, r.reason) for r in result.rejected] == [
        (15.0, "within 8s of a swing that changed the screen")
    ]


def test_keeps_both_swings_when_each_changed_the_screen():
    track_for = windows(session_track([10.0, 15.0], 30))
    result = detect_swings([Onset(10.0, 0.9), Onset(15.0, 0.4)], track_for, 600, CFG, lambda t: True)
    assert [s.impact_s for s in result.swings] == [10.0, 15.0]


def test_falls_back_to_hand_speed_when_the_screen_cannot_be_read():
    track_for = windows(session_track([10.0, 15.0], 30, down_s={15.0: 0.25}))
    result = detect_swings([Onset(10.0, 0.9), Onset(15.0, 0.4)], track_for, 600, CFG, lambda t: None)
    assert [s.impact_s for s in result.swings] == [15.0]
    assert result.rejected[0].reason == "within 8s of a faster swing"


def test_no_screen_change_on_either_keeps_the_faster_and_says_so():
    track_for = windows(session_track([10.0, 15.0], 30, down_s={15.0: 0.25}))
    result = detect_swings([Onset(10.0, 0.9), Onset(15.0, 0.4)], track_for, 600, CFG, lambda t: False)
    assert [s.impact_s for s in result.swings] == [15.0]
    assert result.rejected[0].reason == "no screen change, within 8s of a faster swing"


def test_the_screen_is_checked_in_time_order_and_only_inside_a_span():
    track_for = windows(session_track([10.0, 15.0, 40.0], 60))
    asked: list[float] = []

    def check(t: float) -> bool | None:
        asked.append(t)
        return True

    detect_swings([Onset(15.0, 0.9), Onset(10.0, 0.9), Onset(40.0, 0.9)], track_for, 600, CFG, check)
    assert asked == [10.0, 15.0]


def test_short_clip_ignores_the_screen_check():
    track_for = windows(session_track([2.6, 11.0], 14, down_s={11.0: 0.25}))
    result = detect_swings([Onset(2.6, 1.0), Onset(11.0, 0.7)], track_for, 14, CFG, lambda t: True)
    assert [s.impact_s for s in result.swings] == [11.0]
```

Also check `test_keeps_the_faster_of_two_swings_within_the_gap` still passes with no callback.

- [ ] **Step 3: Run to verify they fail**

Run: `... python -m pytest tests/test_detect.py -q`
Expected: the new tests FAIL with a `TypeError` on the extra argument.

- [ ] **Step 4: Implement in `detect.py`**

Split `detect_swings` into `confirm_candidates` and `keep_swings` per the Interfaces block. In `keep_swings`, build spans by walking the confirmed candidates in time order and starting a new span when the gap to the previous candidate is at least `gap`. For a span of one, or when `screen_changed` is None, or when `duration_s < cfg.short_clip_s`, apply today's rule. Otherwise ask the callback for each candidate in the span in time order, keep every True, then apply the hand-speed rule to the rest against everything already kept in the span, with the three reasons above. Rejections sort by time as today.

- [ ] **Step 5: Run the detection tests**

Run: `... python -m pytest tests/test_detect.py -q`
Expected: PASS.

- [ ] **Step 6: `screen_changed` in `sim.py`, test first**

`tests/test_sim.py`:

```python
def test_screen_changed_needs_four_compared_fields_and_most_of_them_different():
    before = {f: [(0.0, "1")] for f in FIELDS[:4]}
    assert screen_changed(before, {f: "2" for f in FIELDS[:4]}) is True
    assert screen_changed(before, {f: "1" for f in FIELDS[:4]}) is False
    assert screen_changed(before, {**{f: "1" for f in FIELDS[:2]}, **{f: "2" for f in FIELDS[2:4]}}) is True
    assert screen_changed({f: [(0.0, "1")] for f in FIELDS[:3]}, {f: "2" for f in FIELDS[:3]}) is None
```

Half equal counts as changed, since the unchanged rule needs more than half equal. Implement with `compare_before`. Run: PASS.

- [ ] **Step 7: The pipeline check, test first**

In `tests/test_pipeline.py`, using the `screen_video` fixture (IMG_4762's panel for 3 s, then IMG_4761's for 13 s):

```python
def test_screen_change_check_credits_a_change_to_the_first_candidate(screen_video):
    info = probe(screen_video)
    panel = _find_panel(screen_video, info, [1.0], Settings())
    check = _screen_change_check(screen_video, info, panel, Settings(), [1.0, 4.0])
    assert check(1.0) is True  # 4762's numbers before, 4761's after
    assert check(4.0) is False  # the same 4761 numbers it was already credited with


def test_screen_change_check_that_cannot_decode_falls_back(screen_video, monkeypatch):
    info = probe(screen_video)
    panel = _find_panel(screen_video, info, [1.0], Settings())
    monkeypatch.setattr("golf_etl.pipeline.read_frames", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("decode")))
    assert _screen_change_check(screen_video, info, panel, Settings(), [1.0])(1.0) is None
```

Then implement `_screen_change_check` and rewire `detect_video` and `process_video` per the Interfaces block. Run `tests/test_pipeline.py`: PASS, including every existing sim test.

- [ ] **Step 8: Real-footage tests**

In `tests/test_real.py`:

```python
@needs_sim_labels
def test_the_sim_session_keeps_the_shots_that_changed_the_screen():
    video = LOCAL / "IMG_4859.mov"
    detection = detect_video(video, probe(video), Settings())
    impacts = [s.impact_s for s in detection.swings]
    for shot in (36.07, 160.9):  # registered on the sim, rejected before as within 8s of faster hands
        assert any(abs(t - shot) <= 0.5 for t in impacts), shot
    for practice in (39.36, 168.5):  # launched no ball; the screen did not change
        assert not any(abs(t - practice) <= 0.5 for t in impacts), practice
    reasons = {round(r.time_s, 1): r.reason for r in detection.rejected}
    print(reasons.get(232.7))
```

Run with `-s`; record what the 232.7 s candidate did. Then look at frames at 232.7 s and 236 s of the video (`read_frames` into a PNG in the scratchpad) and write in the PR whether the screen changed there and whether the result is right. Run `test_detection_matches_the_labels`: precision and recall must stay 1.0.

- [ ] **Step 9: `docs/design.md`**

Swing detection step 5 becomes: "Confirmed candidates closer than 8s (`GOLF_ONSET_MIN_GAP_S`) are a span. When the video has a sim readout panel, every candidate in a span whose screen changed after it is kept: the fields read 1.5s to 0.25s before the sound against those read 2 to 5s after it, at least 4 fields read on both sides and at most half equal, and a change the screen already showed for an earlier candidate is not credited again. The rest of the span, and every span on a video with no panel, keeps the one with the fastest hands. Videos under 15s keep only the fastest. Only kept swings get pose for the whole clip window. Every other candidate is listed in `session.md` as rejected with the reason." In "Finding the panel", the search runs on the confirmed candidates' times before the dedupe, and the result is reused for the readout. Requirements: add "**Practice swing next to a shot**: a swing the sim registered is kept even when a faster-handed practice swing lands within 8s of it."

- [ ] **Step 10: Commit, suite, PR**

Commits: the `chore(sim)` move, then `feat(detect): keep every swing that changed the sim screen`. PR text: the rule, the crediting, the IMG_4859 result (which shots were recovered, what 232.7 s turned out to be), and that range videos are unchanged.

---

### Task 5: Shaft angle spike, written finding

**Files:**
- Create: `docs/shaft-spike/` with five annotated frames (JPEG, long edge 1280)
- Modify: `docs/design.md` (a new `### Shaft angle (spike)` section after Slicing and frames)
- Scratch only: the spike script in the session scratchpad, never committed

**Interfaces:** none. Nothing ships to `session.md`, `sim.json` or any swing folder.

- [ ] **Step 1: Frames without overlays**

For IMG_4859: `detect_video`, then for each registered swing `pick_positions` and `read_frames` at `takeaway_s` and `halfway_s`, saved as PNGs with the wrist midpoint, both elbows and both wrists written to a JSON sidecar. Twelve swings, two positions each.

- [ ] **Step 2: Fit**

On each frame: crop a square around the wrist midpoint 1.2 leg lengths wide, Canny then `cv2.HoughLinesP`, keep segments longer than 0.3 leg lengths that pass within 0.1 leg lengths of the wrist midpoint and point away from the hips. Record the angle to the horizontal and the angle to each forearm (elbow to wrist). No segment means "not found" with the reason (no long segment, or several disagreeing by more than 10 degrees).

- [ ] **Step 3: Look**

Draw the chosen segment and the angle on the five swings with the clearest frames and save them to `docs/shaft-spike/`. Look at all 24 fits by eye and tally: on the shaft, on something else (body, floor line, mat edge), not found.

- [ ] **Step 4: Write the finding**

A section in `docs/design.md`: what was tried, the tally, the five frames, what breaks the fit, and whether a 5 degree bar looks reachable with another day of work. Plain statements, no numbers presented as measurements of the swing.

- [ ] **Step 5: Commit and PR**

```bash
git commit -s -m "docs: record the shaft angle spike"
```

The PR text is two sentences: it is a finding, nothing in the pipeline changed.

---

## Self-review notes

- Spec items 1 to 5 map to Tasks 1 to 5. The spec's "no backfill" and "requeue local videos" are not code and have no task.
- `Panel` moving to `panel.py` is the one structural change not in the spec; it exists so `detect.py` can carry the panel without importing OpenCV, which the spec requires.
- Names used across tasks: `grade_session`, `smash_value`, `grade_basis`, `venue`, `screen_changed`, `_screen_change_check`, `confirm_candidates`, `keep_swings`, `Detection.panel`. Each is defined in the task that introduces it and used by name afterwards.
