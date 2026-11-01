# Sim Readout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every swing filmed with the simulator screen in view gets `sim.json` with the 12 readout fields read off the screen, and `session.md` gets a per-shot Sim table, so a reviewer never reads numbers from an image.

**Architecture:** A new `golf_etl.sim` module finds the readout panel (a 2 by 6 grid, top right of the sim screen) once per video by matching a reference edge image at several scales, then reads each value cell by glyph templates: per-cell color distance image, threshold, split blobs into glyphs, nearest neighbor against labeled glyphs shipped in the package, and the field's fixed format. Each swing samples frames at 2 a second from 2s after impact until the next swing's clip starts, and a field's value is the read most frames agree on. Glyph templates are built from hand-labeled local videos with a CLI command and committed as a small `.npz`.

**Tech Stack:** Python 3.12, OpenCV and NumPy (already in the image), pytest, ruff.

**Spec:** PRD "golf-etl improvements for first-pass swing analysis", requirement R1 (Claude Doc https://claude.ai/artifact/NpCk36o2cq3qAhz4iu6UJW). This plan covers R1 only; R2 to R12 are separate plans. The algorithm and its numbers come from a spike whose script is kept at `local/sim_spike.py` (gitignored): templates from IMG_4761 read IMG_4762 90/90 fields right, IMG_4762 read IMG_4761 70/70, and both read the slo-mo IMG_4758 29/30 with none wrong.

## Global Constraints

- No AI model, no Tesseract, no new pip or apt dependency: OpenCV and NumPy only. The sim cannot export shot data.
- A field is either read right or left blank. A wrong number is worse than a blank: every rule below that has a choice picks blank.
- Fields, in panel order (row by row, left then right): `club_path`, `carry_game`, `face_to_path`, `carry_raw`, `back_spin`, `total_length`, `smash`, `ball_speed`, `peak_height`, `total_spin`, `face_to_target`, `club_speed`.
- Text as the screen shows it: `4.0 I-O`, `2.0 C`, `0.4 O`, `3837`, `1.43`, `166.0`. Path suffix is `I-O` or `O-I`; face suffix is `C` or `O`.
- Decimals per field: path, faces, carries, total length, speeds, peak height 1; smash 2; spins 0.
- `sim.json` is published to GitHub with the rest of the swing folder (the publisher only excludes `clip.mp4` and `*-small.jpg`).
- Committed glyph file `src/golf_etl/sim_glyphs.npz` stays under 300KB.
- `local/sim_labels.yaml` (gitignored, already written) holds the hand-read labels: 19 shots across `IMG_4761.MOV`, `IMG_4762.MOV` (normal video) and `IMG_4758.MOV` (240fps slo-mo, other camera spot). Carry (game) and total length are not labeled: they count up while the ball flies.
- Commits: `<type>: <subject>` or `<type>(<scope>): <subject>`, subject only, `git commit -s` run as its own command, no AI attribution. Code comments, PR body and test names follow the `writing-as-rumstead` skill. No em or en dashes anywhere. Docs and comments describe what is true now.
- CI is the Dockerfile `test` target: `ruff check src tests && ruff format --check src tests && python -m pytest -q -m "not real"`. Run it in `golf-etl:test` with the repo mounted before each commit: `podman run --rm -v "$PWD":/repo:Z -w /repo golf-etl:test sh -c 'export PYTHONPATH=/repo/src; ruff check src tests && ruff format --check src tests && python -m pytest -q -m "not real"'`.
- Work on branch `feat/sim-readout` off `main`.

## Review Focus

1. The golfer standing in front of part of the panel: covered cells give no read in that frame, uncovered cells still read, and no covered cell ever votes a wrong number. Task 2 adds `test_a_covered_cell_reads_nothing_and_the_rest_still_read`.
2. The previous shot's numbers still on screen in the first frames after impact: the vote picks the value most frames agree on, so two stale frames lose to the rest. Task 4 adds `test_vote_ignores_a_stale_value_outnumbered_by_the_new_one`.
3. A range video with no sim screen: no panel, no `sim.json`, no Sim table, no error. Task 3 adds `test_no_panel_in_a_frame_without_a_screen`; Task 6 adds `test_a_video_without_a_screen_writes_no_sim_json`.
4. Fast hitting, the next swing a few seconds later: the window ends where the next swing's clip starts, and with fewer than 2 frames every field is blank. Task 4 adds `test_shot_window_ends_at_the_next_swings_clip` and `test_one_frame_is_not_enough_to_vote`.
5. Total length showing `--.--` before the ball stops: no read, never a number made of dashes. Task 2 adds `test_dashes_read_as_nothing`.

---

### Task 1: Test fixtures

**Files:**
- Create: `tests/data/sim/` with five PNGs and `labels.yaml`
- Create: `tests/sim_fixtures.py`

**Interfaces:**
- Produces: `tests.sim_fixtures.load(name: str) -> tuple[np.ndarray, dict[str, str | None]]`, the RGB image and its labels (field name to screen text, `None` when unlabeled or blank).

- [ ] **Step 1: Cut the fixtures**

From the local videos, save the panel plus 40px of margin on every side as PNG, RGB, full resolution:

| File | Video | Time | Why |
|---|---|---|---|
| `4761-s1.png` | IMG_4761.MOV | 29.45 | normal video, gray panel |
| `4762-s8.png` | IMG_4762.MOV | 300.4 | odd spin values (119, 309) and `--.--` total length |
| `4758-s1-white.png` | IMG_4758.MOV | 43.24 | slo-mo, white text |
| `4758-s2-tinted.png` | IMG_4758.MOV | 94.18 | slo-mo, colored text from the projector cycle |
| `range.png` | IMG_4634.mov | any frame | no screen |

Panel boxes in 1920x1080 (x0, x1, y0, y1): IMG_4761 and IMG_4762 `(1381, 1507, 203, 446)`, IMG_4758 `(1219, 1323, 247, 451)`. Open each crop and check it shows the panel whole; pick a frame 0.5s later if the golfer covers it. `labels.yaml` maps file name to the 12 fields, taking the 10 labeled values from `local/sim_labels.yaml` for that shot; `null` means not checked (`carry_game`, `total_length`, and every field of `range.png`). Keep the five PNGs under 400KB together.

- [ ] **Step 2: Write `tests/sim_fixtures.py` with `load(name)`**

- [ ] **Step 3: Commit**

```bash
git add tests/data/sim tests/sim_fixtures.py
git commit -s -m "test: add sim panel fixtures"
```

---

### Task 2: Read one value cell

**Files:**
- Create: `src/golf_etl/sim.py`
- Test: `tests/test_sim.py`

**Interfaces:**
- Produces, in `golf_etl.sim`:
  - `FIELDS: tuple[str, ...]` in panel order (Global Constraints).
  - `@dataclass(frozen=True) class Glyphs: images: np.ndarray` (N x 20 x 12 uint8), `aspects: np.ndarray` (N float32), `labels: np.ndarray` (N `<U1`, one of `0123456789ICO`); `save(path: Path) -> None`; `@classmethod load(path: Path) -> Glyphs`.
  - `cut_glyphs(cell: np.ndarray) -> list[str | np.ndarray]`: `"."` or `"-"` for small marks, else the glyph's normalized 12x20 crop.
  - `build_glyphs(samples: Iterable[tuple[np.ndarray, str]], per_class: int = 80) -> Glyphs` from (cell RGB, screen text) pairs; a cell whose glyph count does not match its text is skipped.
  - `read_cell(cell: np.ndarray, field: str, glyphs: Glyphs) -> str | None`: the screen text or `None`.

- [ ] **Step 1: Write the failing tests**

Build the glyphs from the 4 panel fixtures' labeled cells (a module fixture), cut the value cells with the fixture boxes and the cell geometry below, and check. Accuracy on videos the glyphs never saw is Task 5's real test; these pin the mechanics.

```python
def test_reads_every_labeled_cell_of_each_fixture(): ...
    # for each of the 4 panel fixtures: every labeled field reads its label; unlabeled fields are not checked
def test_dashes_read_as_nothing():
    assert read_cell(total_length_cell_of("4762-s8.png"), "total_length", glyphs) is None
def test_a_covered_cell_reads_nothing_and_the_rest_still_read():
    # paint the left column of 4761-s1.png with the cell's own median color
    # left column fields read None, right column fields read their labels
def test_suffix_letters_and_digits_are_told_apart():
    assert read_cell(face_to_target_cell_of("4761-s1.png"), "face_to_target", glyphs) == "4.6 C"
    assert read_cell(face_to_path_cell_of("4758-s1-white.png"), "face_to_path", glyphs) == "1.2 O"
def test_glyphs_round_trip_through_npz(tmp_path): ...
```

Cell geometry (the test helper and Task 3 share it, so put it in `sim.py` as `value_boxes(panel_box) -> list[tuple[int, int, int, int]]`): the panel box split into 2 equal columns and 6 equal rows; each cell inset 3px left and right, 2px from the top; the value is the top `VALUE_SHARE = 0.72` of the cell (the label sits below). If the fixtures disagree with 0.72, pick the share in 0.6 to 0.78 that reads them all and ledger the ruling.

- [ ] **Step 2: Run them, expect FAIL on the import**

- [ ] **Step 3: Implement**

The algorithm is the spike's (`local/sim_spike.py`, `cells`, `glyphs`, `fix`), with these values:

```text
distance image: per pixel, the Euclidean RGB distance from the cell's median color (any text color, any screen tint)
upscale x4 (INTER_CUBIC); lo, hi = 40th and 98th percentile
read at each threshold in THRESHOLDS = (0.7, 0.75, 0.8): mask = dist > lo + t * (hi - lo)
blobs: 8-connected, area > 3 * 4 px, bottom below 30% of the cell height, not touching the left or right edge
hmax = tallest blob; a blob shorter than 0.3 * hmax is a mark ("." when below mid-height and width < 2.2 * height, else "-")
a blob at least 0.6 * hmax tall and k = round(width / (0.52 * hmax)) > 1 wide is cut into k glyphs at the darkest columns
    within 0.25 digit widths of each even split
glyph feature: 12x20 resize (INTER_AREA) of the distance image, mean removed, unit norm, plus aspect width/height
class = argmax(templates @ feature - 0.5 * |template aspect - aspect|)
text: drop marks, map the last 2 glyphs (path) or last 1 (faces) to letters (0 -> O) and the rest to digits (O -> 0),
    put the decimal point back by the field's decimals, path suffix gets its dash, suffix after a space
keep a threshold's read only if it matches the field's format; read_cell returns the read most thresholds agree on, None on a tie or no match
formats: path \d+\.\d (I-O|O-I); faces \d+\.\d [CO]; smash \d\.\d\d; spins \d+; the rest \d+\.\d
```

`Glyphs.images` keeps the raw 12x20 crop scaled to 0 to 255 so the file stays small; `load` restores the normalized features. `build_glyphs` keeps at most `per_class` samples per class, spread evenly over the input order.

- [ ] **Step 4: Run the tests, expect PASS, then the whole suite**

- [ ] **Step 5: Commit**

```bash
git add src/golf_etl/sim.py tests/test_sim.py
git commit -s -m "feat(sim): read a readout cell with glyph templates"
```

---

### Task 3: Find the panel

**Files:**
- Modify: `src/golf_etl/sim.py`
- Create: `src/golf_etl/sim_panel.png` (reference) and add it to `[tool.setuptools.package-data]` in `pyproject.toml`
- Test: `tests/test_sim.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Panel: box: tuple[int, int, int, int]` (x0, x1, y0, y1) and `score: float`; `find_panel(frame: np.ndarray) -> Panel | None`; `value_boxes(box) -> list[tuple[int, int, int, int]]` from Task 2 now takes `Panel.box`.

- [ ] **Step 1: Make the reference**

`sim_panel.png` is the Canny edge image (thresholds 50, 150, on the color distance from the panel's median color) of the IMG_4761 panel at `(1381, 1507, 203, 446)` from the 29.45s frame, exactly the panel box with no margin.

- [ ] **Step 2: Write the failing tests**

```python
def test_finds_the_panel_in_each_fixture():
    # box within 3px of the fixture's known box (40px margin, so (40, 40 + w, 40, 40 + h)) for all 4 panel fixtures
def test_finds_a_scaled_panel_pasted_into_a_blank_frame():
    # 4761-s1 panel scaled 0.8 pasted at (1500, 300) into a 1920x1080 frame of uniform (60, 90, 60): box within 3px
def test_no_panel_in_a_frame_without_a_screen():
    assert find_panel(load("range.png")[0]) is None
```

- [ ] **Step 3: Implement `find_panel`**

Canny edges of the frame the same way, `cv2.matchTemplate(TM_CCOEFF_NORMED)` with the reference resized over scales 0.6 to 1.4 in steps of 0.05, best score wins. `MIN_PANEL_SCORE`: print the best score for the four panel fixtures and `range.png` plus three more frames from IMG_4634 and IMG_4626 (no screen), and set it halfway between the lowest panel score and the highest no-screen score. If the two overlap, stop and report: the reference needs another approach.

- [ ] **Step 4: Run the tests and the whole suite, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/golf_etl/sim.py src/golf_etl/sim_panel.png pyproject.toml tests/test_sim.py
git commit -s -m "feat(sim): find the readout panel"
```

---

### Task 4: Read a shot across frames

**Files:**
- Modify: `src/golf_etl/sim.py`
- Test: `tests/test_sim.py`

**Interfaces:**
- Consumes: `Panel`, `find_panel`, `read_cell`, `value_boxes`, `Glyphs`.
- Produces:
  - `SAMPLE_FPS = 2.0`, `START_AFTER_S = 2.0`, `MAX_AFTER_S = 15.0`
  - `shot_window(impact_s: float, duration_s: float, next_impact_s: float | None, pre_impact_s: float) -> tuple[float, float]`: from `impact_s + START_AFTER_S` to the earliest of `impact_s + MAX_AFTER_S`, `next_impact_s - pre_impact_s`, and the end of the video.
  - `vote(reads: list[str | None]) -> tuple[str | None, int]`: the winner and its count. A winner needs at least 2 votes and at least twice the runner-up's.
  - `@dataclass class SimShot: values: dict[str, str | None]`, `votes: dict[str, int]`, `frames: int`, `first_seen_s: dict[str, float]` (when the winning read first appeared).
  - `read_shot(frames: Iterable[tuple[float, np.ndarray]], panel: Panel, glyphs: Glyphs) -> SimShot`.
  - `write_sim_json(path: Path, shot: SimShot, panel: Panel) -> None`: `{"panel": [x0, x1, y0, y1], "frames": n, "fields": {name: {"text": str | null, "value": float | null, "votes": n, "seen_s": t | null}}}`, `value` being the number in the text.

- [ ] **Step 1: Write the failing tests**

```python
def test_vote_ignores_a_stale_value_outnumbered_by_the_new_one():
    assert vote(["2.0 C", "2.0 C", "5.5 C", "5.5 C", "5.5 C", "5.5 C", "5.5 C"]) == ("5.5 C", 5)
def test_vote_leaves_a_close_split_blank():
    assert vote(["110.0", "110.0", "118.0", "118.0", "118.0"]) == (None, 0)
def test_one_frame_is_not_enough_to_vote():
    assert vote(["1.43", None]) == (None, 0)
def test_shot_window_ends_at_the_next_swings_clip():
    assert shot_window(100.0, 600.0, 106.0, 2.5) == (102.0, 103.5)
    assert shot_window(100.0, 600.0, None, 2.5) == (102.0, 115.0)
    assert shot_window(100.0, 104.0, None, 2.5) == (102.0, 104.0)
def test_read_shot_reads_every_field_from_repeated_frames():
    # 4761-s1 fixture repeated 3 times at t = 2, 2.5, 3: every labeled field matches, frames == 3
def test_sim_json_has_every_field(tmp_path): ...
```

- [ ] **Step 2: Run them, expect FAIL**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run the tests and the whole suite, expect PASS**
- [ ] **Step 5: Commit**

```bash
git add src/golf_etl/sim.py tests/test_sim.py
git commit -s -m "feat(sim): vote each field across the frames after impact"
```

---

### Task 5: Build the glyphs from labeled videos and score them

**Files:**
- Create: `src/golf_etl/sim_eval.py`
- Modify: `src/golf_etl/cli.py` (two subcommands), `pyproject.toml` (package data)
- Create: `src/golf_etl/sim_glyphs.npz`
- Test: `tests/test_cli.py`, `tests/test_real.py`

**Interfaces:**
- Consumes: `local/sim_labels.yaml` (format: `videos: [{path, shots: [{impact, <field>: text}]}]`), Tasks 2 to 4.
- Produces:
  - `labeled_cells(labels_path: Path) -> Iterator[tuple[str, np.ndarray, str, str]]` (video, cell RGB, field, text): per labeled shot, the frames from `shot_window` (no next impact, `pre_impact_s` from `Settings`) at `SAMPLE_FPS`, the panel from the best-scoring `find_panel` over them (the golfer can cover it in some).
  - `score(labels_path: Path, glyphs_for: Callable[[str], Glyphs]) -> tuple[int, int, int, list[str]]`: right, wrong, blank, and one line per miss. `glyphs_for(video)` gives the templates to read that video with.
  - `golf-etl sim-glyphs LABELS OUT` writes `build_glyphs` over every labeled cell.
  - `golf-etl eval-sim LABELS [--glyphs PATH]` prints per video and total right, wrong, blank; without `--glyphs` it holds each video out (templates from the other videos).

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`: `test_sim_glyphs_and_eval_sim_are_subcommands` (parser accepts both, the way the file tests the other subcommands).
`tests/test_real.py` (marked `real`, skipped without `local/sim_labels.yaml`):

```python
def test_sim_reads_held_out_videos_without_a_wrong_number():
    right, wrong, blank, lines = score(SIM_LABELS, held_out_glyphs)
    print("\n".join(lines))
    assert wrong == 0
    assert right >= 0.95 * (right + wrong + blank)
def test_committed_glyphs_read_every_labeled_field():
    right, wrong, blank, _ = score(SIM_LABELS, lambda video: Glyphs.load(GLYPHS))
    assert wrong == 0 and blank == 0
```

- [ ] **Step 2: Run them, expect FAIL**
- [ ] **Step 3: Implement, then build and commit the glyphs**

```bash
podman run --rm -v "$PWD":/repo:Z -w /repo golf-etl:test sh -c 'export PYTHONPATH=/repo/src; python -m golf_etl.cli sim-glyphs local/sim_labels.yaml src/golf_etl/sim_glyphs.npz && python -m golf_etl.cli eval-sim local/sim_labels.yaml'
```
Expected: the npz under 300KB; held-out total wrong 0 and right at least 95% (the spike: 189/190). Add `sim_glyphs.npz` and `sim_panel.png` to package data.

- [ ] **Step 4: Run the real tests and the whole suite, expect PASS**

```bash
podman run --rm -v "$PWD":/repo:Z -w /repo golf-etl:test sh -c 'export PYTHONPATH=/repo/src; python -m pytest -q -m real -k sim'
```

- [ ] **Step 5: Commit**

```bash
git add src/golf_etl/sim_eval.py src/golf_etl/cli.py src/golf_etl/sim_glyphs.npz pyproject.toml tests/test_cli.py tests/test_real.py
git commit -s -m "feat(sim): build glyph templates from labeled videos"
```

---

### Task 6: Write sim.json and the Sim table

**Files:**
- Modify: `src/golf_etl/pipeline.py` (`process_video`), `src/golf_etl/session.py`
- Test: `tests/test_pipeline.py`, `tests/test_session.py`, `tests/test_real.py`

**Interfaces:**
- Consumes: `find_panel`, `shot_window`, `read_shot`, `write_sim_json`, `Glyphs.load` on the packaged `sim_glyphs.npz`, `after_shot_time`, `read_frames(video, info, start, end, None, SAMPLE_FPS)`.
- Produces: `SessionSummary.sims: list[SimShot | None]` (one per swing, empty when there is no panel); `sim.json` in each swing folder when the video has a panel.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pipeline.py
def test_a_video_without_a_screen_writes_no_sim_json(tmp_path):
    # the existing synthetic swing video: no sim.json in any swing folder, no "## Sim" in session.md
def test_a_video_with_a_screen_writes_sim_json(tmp_path):
    # 12s 1920x1080 video made with ffmpeg -loop 1 from a still with the 4761-s1 fixture pasted at its known box,
    # process_video with an injected detect returning one swing at 1.0s: swing-01/sim.json club_speed text "73.2"
# tests/test_session.py
def test_sim_table_lists_every_swing_with_blanks_for_unread_fields(): ...
    # header: | Swing | Club mph | Ball mph | Smash | Carry raw yd | Carry game yd | Total yd | Peak ft | Back spin | Total spin | Path | Face to path | Face to target |
    # a None value is an empty cell
# tests/test_real.py
def test_sim_session_matches_the_labels(tmp_path):
    # process_video on IMG_4761.MOV: every swing's sim.json text equals the label
```

- [ ] **Step 2: Run them, expect FAIL**
- [ ] **Step 3: Implement**

In `process_video`, before the swing loop: read one frame at `after_shot_time` of each of the first 5 swings, run `find_panel` on each, keep the best `Panel` (or none). Per swing with a panel: frames from `shot_window` at `SAMPLE_FPS`, `read_shot`, `write_sim_json(out / "sim.json", ...)`. A failure reading one swing logs and leaves that swing's `SimShot` as `None`, like ball tracking. In `session.md`, after the swing table and before Timing, a `## Sim` section: one sentence that the numbers are read off the simulator screen, a blank cell means the screen was covered or unreadable in most frames, and to check `07-after-shot.jpg` for those; then the table.

- [ ] **Step 4: Run the tests and the whole suite, expect PASS**
- [ ] **Step 5: Commit**

```bash
git add src/golf_etl/pipeline.py src/golf_etl/session.py tests/test_pipeline.py tests/test_session.py tests/test_real.py
git commit -s -m "feat(sim): write sim.json and the Sim table for sessions with a screen"
```

---

### Task 7: Docs and skill

**Files:**
- Modify: `docs/design.md` (outputs list and a short "Sim readout" section: panel match, glyph templates, voting, how to relabel and rebuild with `sim-glyphs`), `claude/skills/golf-swing-analysis/SKILL.md` (step 7 in both modes: use the Sim table and `sim.json` first, open `07-after-shot.jpg` only for blank cells)

- [ ] **Step 1: Edit both, describing what is true now**
- [ ] **Step 2: Commit**

```bash
git add docs/design.md claude/skills/golf-swing-analysis/SKILL.md
git commit -s -m "docs: describe the sim readout"
```

- [ ] **Step 3: Open the PR** (body per `writing-as-rumstead`: what it reads, the held-out numbers from `eval-sim`, and that the skill must be re-uploaded to claude.ai by hand). Merging waits for the owner.
