---
name: golf-swing-analysis
description: Analyze golf swings from uploaded videos or from golf-etl sessions in Google Drive. Use this skill whenever the user uploads a golf swing video (.mov, .mp4, or similar) and asks for coaching feedback, swing analysis, what they're doing wrong, how to improve, or any question about their golf swing mechanics. Also use it whenever the user asks to review a golf session, their latest or recent swings, today's range session, or a folder under golf/sessions in Google Drive, even if they don't say golf-etl. Also trigger when the user says they're at the range and wants swing feedback, or when they share multiple swing videos for comparison. Get the frames (from the session folder or by extracting them from the video), identify the camera angle, ask for ball flight context before diagnosing, and deliver structured coaching feedback with prioritized next steps.
---

# Golf Swing Analysis Skill

A structured coaching workflow for analyzing golf swings from any camera angle, with calibrated confidence and prioritized feedback. Frames come either from a golf-etl session in Google Drive or from a video the user uploads.

---

## Core Principles (from real coaching sessions)

1. **Camera angle first**: state what the angle allows and doesn't allow before diagnosing anything
2. **Ball flight is ground truth**: ask for it before making path/face claims
3. **One root cause, not a list**: most swing faults cascade from one problem; identify it
4. **Compensations are not faults**: early extension, flipping, and chicken wing are usually symptoms, not causes
5. **Calibrate confidence**: say "clearly visible", "likely", or "hard to confirm from this angle" on every call
6. **Strengths first**: always lead with what's working before observations
7. **One priority to fix**: give the golfer one thing to work on, not five

---

## Step 1: Get the Frames

There are two sources. Use 1A when the user mentions a session, their latest or recent swings, or Google Drive. Use 1B when they upload a video.

### 1A: From a golf-etl session in Google Drive

golf-etl turns each uploaded range video into a session folder in Google Drive with frames already picked, labeled, and in order.

1. **Find the session.** Search Google Drive for the folders under `golf/sessions`. Folder names start with the capture date and time (`YYYY-MM-DD-HHMM-<id>`), so the newest name is the latest session. Use the session the user names if they name one.
2. **Read `session.md` first.** It lists the swings with their impact times, sounds that were rejected as not being a swing, and how to read the files.
3. **Choose the swings.** All of them if there are 5 or fewer. Otherwise 5 spread across the session (the first, the last, and evenly in between) unless the user asks for specific swings or for all of them.
4. **For each chosen swing, view `swing-NN/06-sequence.jpg` first.** It is the whole swing in one image, read left to right, top row then bottom row: address, takeaway, halfway back, top, transition, impact, follow-through, finish. Each panel is labeled with the position and its time in the video.
5. **Then view `swing-NN/05-impact-zoom.jpg`.** It is the ball area at full resolution for the frames just before, at, and just after impact, top to bottom. Use it for contact, shaft lean, and strike location.
6. **If there is a simulator or launch monitor screen, read `swing-NN/07-after-shot.jpg`.** It is taken a few seconds after impact, when the screen shows this shot's numbers. Read what is clearly legible: club path, face to path, face to target, club speed, ball speed, smash factor, launch (VLA/HLA), spin (back, side, total), carry, offline, peak height. Only report digits you can actually read; if part of the panel is hidden behind the golfer or blurred, say which numbers you could not read rather than guessing. **Never read numbers from the address, impact, or sequence frames: the screen still shows the previous shot there.**
7. **Open `01-address.jpg` to `04-finish.jpg` only when a call needs a closer look.**
8. **Trust the photo over the skeleton.** The green lines are a pose estimate and can be wrong, especially for hands and arms crossing the body.
9. **Look across swings.** Swings in one session are usually the same club and setup. Base the read on what repeats, and point at a single swing (by its folder name, like `swing-03`) only when it differs.

Then continue with Step 2. Do not run ffmpeg for a session; the frames are already chosen.

### 1B: From an uploaded video

Use ffmpeg to extract frames at 8fps from each uploaded video. Scale to 960x540 for speed.

```bash
mkdir -p /home/claude/swing_frames/<video_id>
ffmpeg -i <video_path> \
  -vf "fps=8,scale=960:540" \
  /home/claude/swing_frames/<video_id>/frame_%03d.jpg \
  -y -loglevel quiet
```

Get total frame count and duration to know which frames to pull:
```bash
ffprobe -v quiet -show_entries format=duration -of default <video_path>
```

For each video, view these key positions (adjust frame numbers based on duration):
- Address/setup: early frames
- Takeaway: ~25% through
- Mid-backswing: ~35%
- Top of backswing: ~45-50%
- Early downswing/transition: ~55%
- Impact zone: ~65-70%
- Follow-through: ~80%
- Finish: final frames

---

## Step 2: Identify Camera Angle

Before any diagnosis, determine the camera angle and state it explicitly:

**True Face-On**: Camera pointing directly at the golfer's chest at address. Shows: weight transfer, hip thrust vs. rotation, shoulder tilt, lateral sway. Does NOT show: swing plane, club path, shaft lean at impact.

**True Down-the-Line (DTL)**: Camera pointing directly down the target line from behind. Shows: swing plane, club path above/below plane, over-the-top, flat/steep. Does NOT show: lateral sway, weight transfer direction.

**In-Between / Oblique**: Most range videos. State clearly: "This angle is between face-on and DTL. I can see [X] reliably but [Y] is harder to confirm. Ball flight will help verify."

**High/Low**: Note if the camera is significantly above or below hip height, as this distorts plane readings.

Do NOT make confident plane or path calls from a non-DTL angle. Do NOT make confident weight transfer calls from a non-face-on angle.

---

## Step 3: Ask for Context (Before Diagnosing)

After identifying the angle, ask the golfer these questions before delivering full analysis. This is not optional: ball flight resolves ambiguity that camera angle cannot:

```
Before I give you the full read, a few quick questions:
1. What club is this?
2. What does the ball typically do: pull, slice, push, draw, something else?
3. What have you already been told or are you already working on?
```

If the user is at the range and wants quick feedback, you can deliver a preliminary visual read while waiting for their answers, but clearly label it as "preliminary" and revisit after they answer.

For a golf-etl session, ask once for the whole session, not once per swing.

When `07-after-shot.jpg` shows simulator or launch monitor numbers, those numbers are the ball flight: use club path, face to path, and face to target to settle path and face questions (for example, a positive club path with a closed face to path is a draw or hook pattern) and do not ask the golfer to describe ball flight for those swings. Still ask which club and what they are working on. Tie every diagnosis back to both the frames and the numbers, and say when they disagree.

---

## Step 4: Deliver Structured Analysis

Structure the output as follows:

### Camera Angle & Confidence
State the angle and what it allows/limits in 2-3 sentences.

### Strengths
List 2-4 genuine positives. Look for:
- Setup/posture quality
- Weight transfer direction and completeness
- Lower body sequencing
- Shoulder turn
- Finish position
- Rhythm and tempo

Do not invent positives. If something is genuinely neutral, leave it out.

### Key Observations
List the 1-3 most significant issues visible from this angle. For each:
- State what you see ("the right shoulder fires toward the target at the start of the downswing")
- State your confidence level ("clearly visible from this angle" / "likely but hard to confirm without DTL" / "ball flight suggests this")
- Do NOT state a compensation as a primary fault

**Common fault cascade to watch for (do not invert):**
- Steep/outside-in path → early extension (hips thrust to move low point) → flip/scoop (hand release to close face)
- The path is the cause. Early extension and flip are almost always downstream.
- Over-the-top typically lives in the TRANSITION (right shoulder firing), not the backswing
- Arm-dominated backswing is often overstated. Look for it but confirm before calling it

### Root Cause
Name the single most upstream fault causing the cascade. This is the one thing fixing everything else.

### One Thing to Work On
Give one drill or feel for the root cause. Not two. Not three. One.

Format:
- **The problem**: what's happening
- **The drill**: specific, concrete, executable at the range
- **The feel**: what it should feel like differently
- **What gets better**: what downstream issues will improve as a result

### What to Leave Alone
Explicitly name 1-2 things the golfer should NOT tinker with. This prevents over-coaching and rabbit holes.

---

## Step 5: Handle Pushback

If the golfer pushes back on a call:
1. Go back to the specific frames immediately. Do not defend from memory
2. Re-examine the relevant position with fresh eyes
3. If they're right, correct clearly: "You're right. Looking again at [frame], [corrected read]. I'll pull back what I said about [X]."
4. Update the root cause and priority if the correction changes the picture
5. Never defend an incorrect call to preserve consistency

---

## Angle-Specific Limitations Reference

| What You're Trying to Assess | Best Angle | Fallback |
|------------------------------|-----------|---------|
| Swing plane / club above or below plane | DTL | Ask about ball flight (slice = steep/over-top) |
| Over-the-top move | DTL | Look for low finish, hands exiting left |
| Early extension (hip thrust) | Face-on | DTL shows posture loss through impact |
| Lateral sway on backswing | Face-on | Hard to confirm from DTL |
| Weight transfer direction | Face-on | Look at trail foot at finish |
| Shoulder tilt at address | Face-on | N/A |
| Shaft lean at impact | DTL | N/A |
| Grip | Either close-up | Ask player |

---

## Handicap Context

Calibrate feedback to the player's level:

**20+ handicap**: Path and contact are the priorities. Grip, posture, and pivot only if they're the direct root cause. Don't add swing thoughts; remove them.

**10-20 handicap**: Path is usually solid or near-solid. Look at transition, shaft lean, and release pattern. One ball-striking variable at a time.

**Under 10**: Precision matters. Plane, attack angle, face control, and sequencing timing. Subtle feels are appropriate.

---

## Red Flags (Things Often Misdiagnosed)

- **Flip at impact** → almost always a compensation for steep path, not a standalone fault. Fix the path.
- **Early extension** → almost always a compensation for steep path or loss of posture. Fix the root.
- **Short/low finish** → symptom of outside-in path and/or deceleration, not a cause.
- **Arm-dominated backswing** → often over-called. Confirm with good shoulder turn evidence before diagnosing.
- **C-posture** → check carefully before calling. Camera angle and clothing can make good posture look rounded.
- **Over-the-top** → the move happens in the transition (right shoulder), not necessarily because the takeaway is bad.

---

## Output Tone

- Direct, practical, no fluff
- One coach talking to one golfer, not a written report
- No bullet point overload; use prose where it flows better
- End with a clear "here's what to do at the range today" statement
