# golf-etl design

golf-etl turns phone videos of golf swings into per-swing frames that Claude can read. A video dropped into a Google Drive folder is picked up, each swing is found and sliced, and labeled frames land back in Drive. Coaching happens in claude.ai: asking to review the latest session triggers the `golf-swing-analysis` skill (kept in this repo under `claude/skills/`), which finds the session in Drive and coaches from its frames.

This repo owns the pipeline. The deployment (namespace, CronJob, SOPS secret, Argo CD Application) lives in [rumstead/homelab](https://github.com/rumstead/homelab) under the `add-golf-etl` OpenSpec change.

## Context
Swing videos come from an iPhone on a tripod. Two shapes show up: long range sessions (5 to 20 minutes, many swings, impact sounds from neighboring bays) and short clips with a single swing. Uploads happen from the range, off the home LAN.

The pipeline runs on a LAN-only homelab cluster with no public ingress, on a worker with plenty of spare CPU and no GPU.

Claude reads images up to 2576px on the long edge (about 3.75MP) on current models. The `golf-swing-analysis` skill asks about ball flight before diagnosing, so the pipeline does not collect a metadata sidecar.

## Goals / Non-Goals
- Goals:
  - Upload from the phone and get per-swing frames in Drive within a few minutes, with no other steps
  - Frames clear enough for Claude to judge body positions at address, top, impact, and finish
  - Never process the same video into two sessions, and reprocess by re-uploading
  - Keep Drive usage bounded on a personal Gmail quota
- Non-Goals:
  - Judging strike location on the clubface. At 120fps slo-mo the contact frame shows up in the impact zoom for wedge-speed swings, but at full driver speed the clubhead still moves about 19cm between frames at 240fps, so it is not guaranteed. Impact tape or foot spray photos are a follow-up
  - Calling the Claude API, notifications, an MCP server
  - Real-time feedback

## Flow
```
phone --share--> Drive golf/inbox/
                       |
     golf-etl poll-drive (every 3 minutes, never overlapping)
       1. recover   anything left in processing/ goes back to inbox/
       2. claim     move the video to processing/
       3. detect    audio onsets, confirmed by fast hands at the sound
       4. slice     impact -2.5s .. +1.5s, re-encoded
       5. frames    address / top / impact / finish, impact zoom, pose sheet
       6. publish   write sessions/.tmp-<id>/, swap into sessions/<id>/
       7. delete    original permanently deleted
       8. sweep     TTLs and size cap
                       |
claude.ai: "review my latest golf session" -> golf-swing-analysis skill
```

## Decisions

### Upload and trigger
- **Google Drive inbox, polled.** Drive push notifications need a public HTTPS webhook, which the cluster does not have. Polling every 3 minutes keeps upload to processing start at about 3 minutes. Alternatives: in-cluster S3 (MinIO or Garage) with an iOS Shortcut needs a way in from off the LAN; a watcher that spawns a Job per video adds moving parts for one upload per range trip.
- **Drive access goes through the `rclone` binary** with rclone's own verified OAuth app and the full `drive` scope (inbox uploads come from the phone, so `drive.file` cannot see them). The owner runs `rclone authorize "drive"` once and the token goes into the environment as `RCLONE_CONFIG_GDRIVE_TOKEN`. Alternatives: a personal Google Cloud OAuth client, which Google would not let a consumer Gmail account use without publishing and branding verification, and a service account, which has no storage quota on a personal Drive. rclone refreshes the access token itself; the refresh token does not expire while it is used.

### Drive layout
```
golf/
  inbox/        phone uploads land here
  processing/   claimed by the running job
  failed/       originals that failed 3 attempts, plus <name>.error.txt
  sessions/
    <session-id>/
      session.md
      swing-01/
        clip.mp4
        01-address.jpg
        02-top.jpg
        03-impact.jpg
        04-finish.jpg
        05-impact-zoom.jpg
        06-sequence.jpg
        07-after-shot.jpg
```
Non-video files in `inbox/` are left alone (strike photos will use them later).

### Identity and idempotency
- **Identity is the Drive `sha256Checksum`**, falling back to `md5Checksum`. Drive computes both on upload, so no download is needed, and filenames like `IMG_1234.MOV` repeat across days. A file with neither checksum yet is skipped until the next poll.
- **Session ID** is `<capture time YYYY-MM-DD-HHMM>-<first 8 hex of the hash>`. Capture time comes from the video's `creation_time` (ffprobe), falling back to the file's modified time in Drive. Each session folder holds a `.golf-etl.json` marker with `{sha256, sourceName, pipelineVersion, published}`; a session is found by listing folder names (which end in the hash prefix) and confirming the marker. Retry counts live in `golf/.golf-etl/state.json`, which is safe with a single writer since runs never overlap.
- **Claiming**: a video is moved from `inbox/` to `processing/` before any work. Runs never overlap and a failed run is not restarted in place (see Runtime contract), so anything in `processing/` when a run starts belongs to a dead run. It is moved back to `inbox/` with its retry count incremented and retried in the same run.
- **Atomic replace**: output is written to `sessions/.tmp-<session-id>/`. On success any existing `sessions/<session-id>/` is permanently deleted and the temp folder is renamed into place.

| Case | Behavior |
|---|---|
| Same video in the inbox twice in one poll | Process one copy, permanently delete the others, note it in `session.md` |
| Re-uploaded after a successful run | Reprocess and replace `sessions/<session-id>/` |
| Re-uploaded after landing in `failed/` | Delete the copy in `failed/` and its error file, reset attempts, process normally |
| Different videos with the same filename | No conflict, identity is the hash |

None of these are errors. A copy uploaded while a run is processing the same video is picked up by the next run as a re-upload.

### Swing detection
1. ffmpeg extracts the first decodable audio track as 48kHz mono. iPhones put an APAC spatial audio track first that ffmpeg cannot decode, so it is skipped. librosa onset detection runs on a high-passed signal (about 2kHz) to suppress voices and wind, and each onset is backtracked from the envelope peak to where the transient starts.
2. Onsets are moved earlier by `GOLF_IMPACT_AUDIO_LAG_MS` (default 12). The sound reaches the phone after contact and the phone adds its own audio/video offset; on the first two labeled clips the raw transient trailed the visible contact frame by 17 to 23ms.
3. Candidates closer than 8s to a stronger candidate are dropped. Dropped ones with strength 0.5 or more are listed in `session.md` as rejected so a missed swing is visible.
4. MediaPipe Pose Landmarker (lite) runs on the clip window around each candidate (impact minus 2.5s to plus 1.5s) at 640px and at most 60fps, never on the whole video. The largest person in frame is the golfer. A candidate is confirmed when the hands (midpoint of both wrists, which works for either handedness) move at least 1.0 frame heights per second within 300ms of the onset. Where the hands are fastest overall does not matter: in a 12 minute simulator session, 10 of 21 swings had their fastest hands in the follow-through, 400 to 1300ms after impact. Sounds made while the hands are slow (a club soled or tapped at address) are rejected. Unconfirmed candidates are recorded in `session.md` as rejected with the reason.
5. Videos under 15s keep only the single strongest confirmed candidate.
6. Zero confirmed swings is not a failure. The session is written with `session.md` only.

All thresholds live in a config module with environment variable overrides (`GOLF_ONSET_MIN_GAP_S`, `GOLF_CONFIRM_WINDOW_MS`, and so on) so tuning does not need a rebuild.

### Slicing and frames
- **Clip**: impact minus 2.5s to impact plus 1.5s, re-encoded (stream copy snaps to keyframes) to 1080p long edge, H.264 CRF 23, AAC. About 3MB each. The clip is for the owner, not for Claude.
- **Frames are decoded from the original video**, never from the re-encoded clip. HDR video (HLG or PQ, the iPhone default) is tone mapped to SDR BT.709 so frames are not flat and washed out. Portrait rotation is applied.
  - Address: end of the last still stretch (0.2s or more) of the hands before the top
  - Top: highest hands in the 2s before impact
  - Impact: frame nearest the corrected audio onset
  - Finish: highest hands from 0.3s after impact to the end of the clip window
  - For address, top, and finish, the sharpest frame (Laplacian variance) within plus or minus 2 frames of the target is used. Impact is never moved.
- **Images**: JPEG quality 90, sRGB, metadata stripped, label and timestamp burned into a corner. Every image fits 2560px on the long edge and 3.7MP (`GOLF_FRAME_MAX_PIXELS`).
  - `01` to `04`: full frame, 2560px long edge (never upscaled), no overlay
  - `05-impact-zoom.jpg`: a native-resolution band at ball height (centered on the ankles, a quarter of the frame tall, the frame width up to 1.2 frame heights) for impact minus 1, impact, and impact plus 1, stacked. The ball sits between the feet face-on but past the toes down the line, so the band covers both instead of guessing
  - `06-sequence.jpg`: the whole swing in one image, two rows of four in reading order: address, takeaway, halfway back, top, transition, impact, follow-through, finish. Takeaway and halfway back sit at a third and two thirds of the time from address to top, transition halfway from top to impact, follow-through halfway from impact to finish. Each panel has the pose skeleton and a label drawn after resizing so it stays legible, and the sheet fits 2560px and 3.7MP
- `07-after-shot.jpg`: one full-resolution frame `GOLF_AFTER_SHOT_S` (default 8s) after impact, pulled earlier when the next swing's clip window starts sooner and kept inside the video. On a simulator the screen shows the previous shot until about 2s after impact and this shot's final numbers (carry, total) by about 8s, so this is the only frame whose numbers belong to the swing. On the range it is just the golfer after the shot.
- `session.md` lists source name, capture time, hash, pipeline version, each swing with its impact timestamp and confidence, rejected candidates, and removed duplicates. When there are swings it also carries a short "How to read this session" section naming each file, its order, and what it is good for, so any chat that reads it knows what to do without instructions.
- Recording tip for the README: in daylight, 240fps slo-mo usually helps the impact frames more than 4K does because the faster shutter cuts blur. Both work.

### Drive storage
- Originals are removed with `files.delete` (permanent) after the session is in place. Trashed files still count against quota.
- A sweep at the end of every run:
  - `failed/` originals older than 3 days are permanently deleted (`GOLF_FAILED_TTL_DAYS`)
  - `clip.mp4` files older than 14 days are permanently deleted (`GOLF_CLIP_TTL_DAYS`)
  - session folders older than 60 days are permanently deleted (`GOLF_SESSION_TTL_DAYS`)
  - if session files still total over 3GiB (`GOLF_MAX_BYTES`), the oldest sessions are deleted until they are under. Failed originals do not count, since they expire in 3 days anyway and one large failed upload should not push every session out
  - the sweep is one recursive listing of `sessions/`: a clip is any `clip.mp4`, a session's age is its marker's time, and failed originals age from their `.error.txt` (a video's own time is when it was shot)
- Rough sizes: a 30-swing session is about 200MB on day one and about 120MB once clips expire.

### Failures
- Each video is processed inside its own try block. On an exception `attempts` is incremented and the video goes back to `inbox/`. On the third failure it moves to `failed/` with `<name>.error.txt` holding the exception and the last log lines.
- Crashes the process cannot catch (OOMKill, a deadline kill) are handled by the recovery step at the start of the next run.

## Runtime contract
Whatever schedules `golf-etl poll-drive` must guarantee:
- Runs never overlap (`concurrencyPolicy: Forbid`)
- A failed run is not restarted in place (`restartPolicy: Never`, `backoffLimit: 0`), so recovery at startup is safe
- About 10Gi of scratch disk (4K/60 is about 400MB per minute) and up to 3Gi of memory
- `RCLONE_CONFIG_GDRIVE_TYPE=drive`, `RCLONE_CONFIG_GDRIVE_SCOPE=drive`, `RCLONE_CONFIG_GDRIVE_TOKEN` (the JSON from `rclone authorize`) and `RCLONE_DRIVE_USE_TRASH=false` in the environment, and optionally `GOLF_REMOTE` (default `gdrive:golf`) plus any threshold or TTL overrides

## Repo
- Python 3.12, `requirements.txt` with exact pins, Dockerfile on `python:3.12-slim` with ffmpeg and the bundled Pose Landmarker lite model.
- CLI:
  - `golf-etl process <video> --out <dir>`: local run, no Drive
  - `golf-etl eval <labels.yaml>`: detection precision, recall, and timing error against hand-labeled impact timestamps
  - `golf-etl poll-drive`: the scheduled entrypoint
- Tests:
  - onset detection against synthetic audio (clicks plus noise)
  - frame selection against canned pose sequences
  - every row of the idempotency table and every failure scenario against a fake Drive client; the Drive layer sits behind a small interface for this
  - synthetic videos generated with ffmpeg inside the tests (test pattern, clicks, HDR, rotation)
  - real footage kept out of git in `local/` with a `local/labels.yaml` of hand-labeled impact times; `pytest -m real` runs eval and full processing on it and skips when it is missing
- GitHub Actions runs tests and pushes `ghcr.io/rumstead/golf-etl:latest` on `main`. The cluster pulls `latest` with `imagePullPolicy: Always`, so there are no version tags to bump.
- Renovate (`renovate.json5`): `config:recommended` with the `pip_requirements`, `dockerfile`, and `github-actions` managers, Monday before 6am America/New_York like homelab, with `mediapipe`, `opencv-*`, and `numpy` grouped since their versions are coupled.

## Requirements
These are the behaviors the tests pin down.

### Drive inbox ingestion
The pipeline polls `golf/inbox` and turns each video into a session under `golf/sessions/`.
- **Video uploaded**: when a video finishes uploading to `golf/inbox`, processing starts on the next poll and a session folder appears when it completes.
- **Non-video file**: a file that is not a video is left in place unchanged.

### Content-based idempotency
A video is identified by its Drive checksum, not its filename. One video maps to at most one session folder.
- **Duplicate copies in one poll**: exactly one is processed, the others are permanently deleted and listed in `session.md`.
- **Re-upload after success**: the video is reprocessed and the existing session folder replaced. No second folder is created.
- **Same filename, different video**: each produces its own session.
- **Crash while replacing a session**: the previous session folder stays intact and readable.

### Swing detection
Swings are found by audio onsets confirmed by fast hands at the sound.
- **Range session with neighboring bays**: each of the owner's swings is emitted separately, and unconfirmed onsets are listed in `session.md` as rejected with timestamps.
- **Impact timing**: the impact frame is within two frames of the first frame showing the clubface on the ball, on 120fps footage.
- **Single-swing clip**: a video under 15 seconds emits at most one swing.
- **No swing found**: the session is written with `session.md` stating zero swings, and it is not a failure.

### Swing frames for Claude
Each swing gets a clip and labeled JPEGs decoded from the original video, 2560px long edge or smaller and never upscaled.
- **Detected swing**: the folder contains `clip.mp4` and `01-address.jpg` through `06-sequence.jpg`, each JPEG sRGB and labeled with position and timestamp.
- **Impact zoom**: `05-impact-zoom.jpg` shows a native-resolution band at ball height for the frames before, at, and after impact, with the ball in it from either camera angle.
- **iPhone footage**: HDR, portrait, APAC-plus-AAC `.mov` files process like any other video, with natural color.
- **Blurry target frame**: for address, top, and finish, a sharper frame within two frames is used instead.

### Drive storage bounds
- **Original after success**: permanently deleted, not trashed.
- **Expiry sweep**: at the end of a run, failed originals older than 3 days, clips older than 14 days, and sessions older than 60 days are permanently deleted, honoring overrides.
- **Size cap**: if session files total over 3GiB after the sweep, the oldest sessions are deleted until they are under. Failed originals never evict sessions.

### Failure handling
- **Processing error**: on the first or second failure the video returns to `golf/inbox` with its attempt count incremented.
- **Third failure**: the video moves to `golf/failed/` with `<name>.error.txt`.
- **Run killed mid-video**: the next run moves it back to `golf/inbox` and counts the attempt.
- **Re-upload of a failed video**: the failed copy and its error file are deleted and the new upload starts with a fresh attempt count.

## Coaching skill
`claude/skills/golf-swing-analysis/SKILL.md` is the owner's claude.ai skill with a second way in. It triggers on requests like "review my latest golf session", finds the newest folder in `golf/sessions` (names sort by capture time), reads `session.md`, picks up to 5 swings spread across the session unless told otherwise, views each swing's `06-sequence.jpg` and `05-impact-zoom.jpg`, reads simulator or launch monitor numbers from `07-after-shot.jpg` when a screen is in view (and never from earlier frames, which show the previous shot), opens the full key frames only when needed, asks about ball flight once per session when there are no numbers, and then runs the existing coaching steps. It is uploaded to claude.ai by hand; claude.ai's copy is the one that runs.

## Risks / Trade-offs
- claude.ai's Drive connector might not hand JPEGs to Claude as images → checked first with a test image, before any pipeline code. If it fails, the fallback is calling the Claude API from the pipeline.
- `latest` with `Always` means a bad push breaks the next run → failures are retried and land in `failed/`, and the original is kept for 3 days.
- Audio onsets at a busy range → pose confirmation plus the eval harness. Rejected candidates in `session.md` make misses visible.
- Full `drive` scope through rclone's shared OAuth app → the token only reaches the owner's account and lives only in the homelab SOPS secret; rclone's app shares Google API quota with other rclone users, which a few calls every 3 minutes does not approach.

## Build order
1. Spike: confirm claude.ai reads a 2560px JPEG through the Drive connector.
2. Local pipeline: `process` and `eval`, tuned on a few labeled range sessions.
3. Drive poller with idempotency, failures, and retention.
4. CI image push and Renovate.
5. Deploy through homelab.

## Follow-ups
- Strike photos: impact tape or foot spray photos in the inbox, attached to the session recorded just before them
- Notification when a session is ready
- Claude API analysis and an MCP server for sessions
