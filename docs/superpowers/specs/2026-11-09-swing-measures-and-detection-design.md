# Matched swing positions, sim-aware grading and detection by screen change

## Intent

The owner's coaching work is early hinge, more turn and weight shift, and the main miss is toe contact. The 2026-11-08 sim session (IMG_4859, session `2026-11-08-1613-86c76b86` in `rumstead/golf-sessions`) showed what the session output can and cannot say about that:

- The takeaway panel is a fixed 0.33 s before the top, so it shows backswing speed, not a position. Panels cannot be compared across swings, and the hinge change at swing 9 was invisible.
- Two real shots near 0:36 and 2:41 were rejected as "within 8s of a faster swing" while two practice swings that launched no ball were kept.
- Swings 11 and 15 were graded poor from ball pixels per frame while the sim read 111 and 112 mph, the fastest balls of the day.
- Nothing in the output says whether the session was at the sim or the range, and camera setups differ between them.

This design covers the five items from the owner's handoff doc ("golf-etl handoff: detection and data fixes", Claude Doc `PnCWLoAzjC18LDDCMqdz4e`) that need only the camera and the sim screen the pipeline already reads. Item 6 of that doc, the Club Path regression, shipped as PR #21 on 2026-11-09.

## What was verified

- `pick_positions` in `frames.py` already computes `at_hips`, the first time after address the hands reach the hip line going back, and uses it only to look back for the takeaway start. The hip line falls back to 0.1 leg lengths above the address hand height for a golfer who addresses with the hands at hip height.
- `CSV_JOINTS` in `pose.py` writes nose, shoulders, wrists, hips, knees and heels. MediaPipe landmarks 13 and 14 are the elbows.
- `detect_swings` in `detect.py` confirms every onset on pose, then keeps the fastest hands within `onset_min_gap_s` (8 s). The sim readout code in `sim.py` already has an "unchanged screen" rule: frames from 1.5 s to 0.25 s before impact compared with the voted window, at least 4 fields read on both sides and more than half equal means no shot registered.
- The panel search (`_find_panel` in `pipeline.py`) runs after detection on frames at impact + 3, 4 and 5 s of the first five swings.
- `grade` in `ball.py` grades ball px/frame against the session median (good at 0.9 times the median or more, poor under 0.78). `session.md` and `ball.json` carry the grade and the thresholds.
- On IMG_4859 the sim read smash on 12 shots: 1.08 and 1.15 on the two the owner called poor strikes, 1.27 to 1.42 on the rest, median about 1.35.
- IMG_4859.mov (sha256 `86c76b86...`) is in `local/` with its 12 registered shots hand-labeled in `local/sim_labels.yaml`. The committed glyphs read it at 117 of 120 fields, 0 wrong.
- The sim screen updates about 2 s after impact (swing 9: impact 260.3 s, first read 262.28 s).

## Goals

- Panels line up across swings: the takeaway panel is the hands reaching hip height going back.
- `pose.csv` carries the elbows so hinge can be measured as numbers.
- A shot that changed the sim screen is never rejected in favor of a nearby practice swing.
- On sim days the grade reflects strike quality from the sim's numbers, not ball pixels.
- Every new session says whether it was at the sim or the range.
- A bounded spike finds out whether the shaft angle at hip and shoulder height can be fitted from these frames, and writes down what it found. No shaft numbers ship from this round.

## Non-goals

- Spoken notes, transcription, or any notes field. The owner tells the analysis agent the club and what they were working on.
- The left column of the sim screen (launch angle, horizontal launch, side spin, offline).
- Strike location from the sim's impact replay. IMG_4858 shows it on a PC monitor with the replay pane half out of frame; it needs its own capture first.
- Attack angle. That is a question for the sim's settings.
- Better indoor ball tracking. Sim grading replaces it on sim days; the range keeps px/frame.
- Changes to the golf-swing-analysis skill. The venue tag exists so the skill can compare within a venue; teaching it to do so is separate work in `ai-loadout`.
- Rewriting sessions already published in `rumstead/golf-sessions`. The venue tag and grade basis appear on sessions processed after the PRs land. The videos still in `local/` can be requeued through the inbox afterwards, as IMG_4859 was on 2026-11-09, which rewrites their folders with no extra code. (The owner was not asked; this is the designer's call.)

## Anti-goals

- A well-formed wrong number in any output. Blank with a reason beats wrong, as everywhere else in this pipeline.
- A regression on the existing videos: the detection eval on `local/labels.yaml`, the held-out sim eval (305 of 310 right, 0 wrong across the four labeled videos with the committed glyphs), and the real-footage tests on the `local/` videos must not get worse.
- Slower range processing. Range videos have no panel, so the screen-change work must cost them nothing beyond one failed panel search.
- A grade that mixes two bases in one table.

## Constraints

- Python, OpenCV and MediaPipe as today. No model, no new service.
- TDD: every change starts with a failing test. Tests run in the container (`golf-etl:test` with `PYTHONPATH=/repo/src`), and the real-footage tests use the videos in `local/`.
- One PR per item below, in the order listed, each small enough to read in one sitting. Commit and PR text follow the `writing-as-rumstead` skill.
- `docs/design.md` describes what is true after each PR.

## The items

### 1. Venue tag

`process_video` knows whether a panel was found. Write `- Venue: sim` or `- Venue: range` in the `session.md` header after the Swings line, and `"venue"` in the session's `.golf-etl.json`. A session with no panel is `range`, including a sim session where the screen was out of frame (IMG_4626 face-on). The "How to read" text gets one sentence saying the tag exists and that camera setups differ between venues.

### 2. Grade from sim smash on sim days

When a session has at least 3 swings with a sim smash read, every grade in that session comes from smash against the session median of the read smashes: good at 0.97 times the median or more, poor under 0.92 times it, fair between. A swing on such a session with no smash read gets no grade. Sessions below 3 reads grade from ball px/frame as today.

The thresholds are the builder's starting point, to be tuned against the owner's calls: on IMG_4859 the two strikes the owner called poor (swings 1 and 8) must grade poor and the last seven swings must not. The thresholds become settings like `ball_good` and `ball_poor`.

`ball.json` gains `"basis": "sim_smash"` or `"ball_px"` next to the thresholds, and the session summary line says which basis the grades use ("graded by sim smash factor" or the current px/frame wording). The Grade column stays where it is. The "How to read" text explains both bases in one sentence each.

### 3. Takeaway at hip height, elbows in the pose file

`pick_positions` sets `takeaway_s` to `at_hips` when the hands reach the hip line between address and the top, and keeps the current fixed time before the top as the fallback when they do not (hips not in the pose, or the hands never reach the line). The takeaway start logic does not change. The sequence sheet label stays "takeaway". The "How to read" text describes the takeaway as the hands reaching hip height going back, so it lines up with halfway back, and names the fallback.

`CSV_JOINTS` adds `left_elbow` and `right_elbow` between the shoulders and the wrists. Consumers read `pose.csv` by header. The "How to read" text lists the elbows.

Done when the real-footage test on IMG_4859 shows the takeaway panel's hand height within a small band across the registered swings (the builder picks the band from the pose track), and a swing without hips in the pose still gets a takeaway panel.

### 4. Keep every swing that changed the sim screen

Detection stays audio onsets confirmed by pose. The change is in the dedupe. Within a span of confirmed candidates closer than `onset_min_gap_s` to each other, a candidate is kept when the sim screen changed after it, and the hand-speed rule applies only among candidates with no screen change or where the screen cannot be read.

"The screen changed after it" reuses the sim module's unchanged-screen rule in reverse: the fields read from frames 1.5 s to 0.25 s before the onset against the fields read from frames at the onset plus 2 s to plus 5 s, sampled twice a second; at least 4 fields read on both sides and more than half different. Each change is credited to one candidate: when two candidates in a span show the same after-screen, the earlier one gets the change and the later one falls back to the hand-speed rule. This is what keeps a practice swing out when it lands right after a real shot and its before-window straddles the screen update.

The panel box has to exist before the dedupe, so the pipeline order becomes: confirm candidates, find the panel from the after-frames of the confirmed candidates (the same agreement rule as today, just fed candidate times instead of swing times), run the dedupe with a screen-change callback, then write swings. On a range video the panel search finds nothing, the callback is never called, and behavior is exactly today's. `detect.py` must not import OpenCV or the sim module: the callback is passed in.

Rejections get a reason that says what happened: "within 8s of a swing that changed the screen" and "no screen change, within 8s of a faster swing".

Done when reprocessing IMG_4859 includes the shots near 36.07 s and 160.9 s, the 232.7 s candidate has been checked by eye and the result recorded in the PR, the detection eval on `local/labels.yaml` does not lose a labeled swing, and a range video's detection output is unchanged.

### 5. Shaft angle spike, written finding only

A bounded spike, one day at most, on IMG_4859's takeaway (hip-height) and halfway-back frames rendered without the skeleton overlay. Fit the shaft as a line from the hands (wrist midpoint) toward the clubhead and report its angle to the horizontal in the image and its angle to the lead forearm (elbow to wrist), drawn on the five frames for the owner to look at.

The deliverable is a written finding, as a short section in `docs/design.md`: what was tried, how often a line was found, the five frames with the fitted line, and whether a 5 degree bar looks reachable. No shaft numbers go into `session.md`, `sim.json` or any other output, because an agent cannot verify a protractor-grade angle on its own and the owner chose not to be the checker. Shipping the measurement is a later decision made from the finding.

## Left to the builder

- Where the screen-change read lives (the sim module already has the frame sampling and the field comparison; the builder decides how the pipeline hands it to the dedupe as a callback).
- Exact grade thresholds within the rule above, and the takeaway band in the real-footage test.
- Test fixtures: a synthetic pose track where the hands reach the hips at a known time, a candidate span where one candidate changes the screen and a later one straddles the update, a session with and without a panel for the venue tag and the grade basis.
- The shaft fit method within the spike.
