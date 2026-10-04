# golf-etl design

golf-etl turns phone videos of golf swings into per-swing frames that Claude can read. A video dropped into a Google Drive folder is picked up, each swing is found and sliced, and labeled frames land back in Drive. Coaching happens in claude.ai, where a chat is pointed at the session folder and the `golf-swing-analysis` skill does the analysis.

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
  - Strike location on the clubface from video. At 45 m/s the clubhead moves about 75cm between frames at 60fps and about 19cm at 240fps, so the contact frame almost never exists. Impact tape or foot spray photos are a follow-up
  - Calling the Claude API, notifications, an MCP server
  - Real-time feedback

## Flow
```
phone --share--> Drive golf/inbox/
                       |
     golf-etl poll-drive (every 3 minutes, never overlapping)
       1. recover   anything left in processing/ goes back to inbox/
       2. claim     move the video to processing/
       3. detect    audio onsets, confirmed by a pose wrist speed peak
       4. slice     impact -2.5s .. +1.5s, re-encoded
       5. frames    address / top / impact / finish, impact zoom, pose sheet
       6. publish   write sessions/.tmp-<id>/, swap into sessions/<id>/
       7. delete    original permanently deleted
       8. sweep     TTLs and size cap
                       |
claude.ai: "look at my latest session in golf/sessions"
```

## Decisions

### Upload and trigger
- **Google Drive inbox, polled.** Drive push notifications need a public HTTPS webhook, which the cluster does not have. Polling every 3 minutes keeps upload to processing start at about 3 minutes. Alternatives: in-cluster S3 (MinIO or Garage) with an iOS Shortcut needs a way in from off the LAN; a watcher that spawns a Job per video adds moving parts for one upload per range trip.
- **Drive auth is an OAuth refresh token for the owner's account**, from a Desktop OAuth client in a personal GCP project with the `drive` scope. Service accounts have no storage quota on a personal Gmail Drive, so their uploads fail. `drive.file` cannot delete files the owner uploaded from the phone. The consent screen must be in Production status, since refresh tokens for apps in Testing status expire after 7 days.

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
        06-pose-sheet.jpg
```
Non-video files in `inbox/` are left alone (strike photos will use them later).

### Identity and idempotency
- **Identity is the Drive `sha256Checksum`**, falling back to `md5Checksum`. Drive computes both on upload, so no download is needed, and filenames like `IMG_1234.MOV` repeat across days. A file with neither checksum yet is skipped until the next poll.
- **Session ID** is `<capture time YYYY-MM-DD-HHMM>-<first 8 hex of the hash>`. Capture time comes from the video's `creation_time` (ffprobe), falling back to Drive `createdTime`. The session folder carries `appProperties` `{sha256, sourceName, pipelineVersion}` and is found with an `appProperties has {key='sha256' and value='...'}` query.
- **Claiming**: a video is moved from `inbox/` to `processing/` before any work. Runs never overlap and a failed run is not restarted in place (see Runtime contract), so anything in `processing/` when a run starts belongs to a dead run. It is moved back to `inbox/` with `appProperties.attempts` incremented.
- **Atomic replace**: output is written to `sessions/.tmp-<session-id>/`. On success any existing `sessions/<session-id>/` is permanently deleted and the temp folder is renamed into place.

| Case | Behavior |
|---|---|
| Same video in the inbox twice in one poll | Process one copy, permanently delete the others, note it in `session.md` |
| Re-uploaded after a successful run | Reprocess and replace `sessions/<session-id>/` |
| Re-uploaded after landing in `failed/` | Delete the copy in `failed/` and its error file, reset attempts, process normally |
| Different videos with the same filename | No conflict, identity is the hash |

None of these are errors. A copy uploaded while a run is processing the same video is picked up by the next run as a re-upload.

### Swing detection
1. ffmpeg extracts audio as 22.05kHz mono. librosa onset detection runs on a high-passed signal (about 2kHz) to suppress voices and wind. Candidates closer than 8s to a stronger candidate are dropped.
2. MediaPipe Pose Landmarker (lite) runs only on a window of plus or minus 1.5s around each candidate, not on the whole video. A candidate is confirmed when lead wrist speed peaks within 300ms of the onset. Unconfirmed candidates are recorded in `session.md` as rejected with their timestamps.
3. Videos under 15s keep only the single strongest confirmed candidate.
4. Zero confirmed swings is not a failure. The session is written with `session.md` only.

All thresholds live in a config module with environment variable overrides (`GOLF_ONSET_MIN_GAP_S`, `GOLF_CONFIRM_WINDOW_MS`, and so on) so tuning does not need a rebuild.

### Slicing and frames
- **Clip**: impact minus 2.5s to impact plus 1.5s, re-encoded (stream copy snaps to keyframes) to 1080p long edge, H.264 CRF 23, AAC. About 3MB each. The clip is for the owner, not for Claude.
- **Frames are decoded from the original video**, never from the re-encoded clip.
  - Address: last low-motion window of the wrists before takeaway
  - Top: wrist height peak or direction reversal between address and impact
  - Impact: frame nearest the audio onset
  - Finish: where motion settles after impact, capped at plus 1.5s
  - For address, top, and finish, the sharpest frame (Laplacian variance) within plus or minus 2 frames of the target is used. Impact is never moved.
- **Images**: JPEG quality 90, sRGB, metadata stripped, label and timestamp burned into a corner.
  - `01` to `04`: full frame, 2560px long edge (never upscaled), no overlay
  - `05-impact-zoom.jpg`: native-resolution crop around the ball area (ankle midpoint from the address pose) for impact minus 1, impact, and impact plus 1, side by side, scaled down only if the strip exceeds 2560px
  - `06-pose-sheet.jpg`: 2x2 grid of the four positions with the pose skeleton drawn, 2560px wide
- `session.md` lists source name, capture time, hash, pipeline version, each swing with its impact timestamp and confidence, rejected candidates, and removed duplicates.
- Recording tip for the README: in daylight, 240fps slo-mo usually helps the impact frames more than 4K does because the faster shutter cuts blur. Both work.

### Drive storage
- Originals are removed with `files.delete` (permanent) after the session is in place. Trashed files still count against quota.
- A sweep at the end of every run:
  - `failed/` originals older than 3 days are permanently deleted (`GOLF_FAILED_TTL_DAYS`)
  - `clip.mp4` files older than 14 days are permanently deleted (`GOLF_CLIP_TTL_DAYS`)
  - session folders older than 60 days are permanently deleted (`GOLF_SESSION_TTL_DAYS`)
  - if `golf/` is still over 3GiB (`GOLF_MAX_BYTES`), the oldest sessions are deleted until it is under
- Rough sizes: a 30-swing session is about 200MB on day one and about 120MB once clips expire.

### Failures
- Each video is processed inside its own try block. On an exception `attempts` is incremented and the video goes back to `inbox/`. On the third failure it moves to `failed/` with `<name>.error.txt` holding the exception and the last log lines.
- Crashes the process cannot catch (OOMKill, a deadline kill) are handled by the recovery step at the start of the next run.

## Runtime contract
Whatever schedules `golf-etl poll-drive` must guarantee:
- Runs never overlap (`concurrencyPolicy: Forbid`)
- A failed run is not restarted in place (`restartPolicy: Never`, `backoffLimit: 0`), so recovery at startup is safe
- About 10Gi of scratch disk (4K/60 is about 400MB per minute) and up to 3Gi of memory
- `GOLF_DRIVE_CLIENT_ID`, `GOLF_DRIVE_CLIENT_SECRET`, `GOLF_DRIVE_REFRESH_TOKEN` in the environment, and optionally `GOLF_ROOT_FOLDER` (default `golf`) plus any threshold or TTL overrides

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
  - a few small real fixtures checked in, full range sessions kept local and referenced from `labels.yaml`
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
Swings are found by audio onsets confirmed by a wrist speed peak.
- **Range session with neighboring bays**: each of the owner's swings is emitted separately, and unconfirmed onsets are listed in `session.md` as rejected with timestamps.
- **Single-swing clip**: a video under 15 seconds emits at most one swing.
- **No swing found**: the session is written with `session.md` stating zero swings, and it is not a failure.

### Swing frames for Claude
Each swing gets a clip and labeled JPEGs decoded from the original video, 2560px long edge or smaller and never upscaled.
- **Detected swing**: the folder contains `clip.mp4` and `01-address.jpg` through `06-pose-sheet.jpg`, each JPEG sRGB and labeled with position and timestamp.
- **Impact zoom**: `05-impact-zoom.jpg` shows native-resolution crops of the ball area for the frames before, at, and after impact.
- **Blurry target frame**: for address, top, and finish, a sharper frame within two frames is used instead.

### Drive storage bounds
- **Original after success**: permanently deleted, not trashed.
- **Expiry sweep**: at the end of a run, failed originals older than 3 days, clips older than 14 days, and sessions older than 60 days are permanently deleted, honoring overrides.
- **Size cap**: if `golf/` is over 3GiB after the sweep, the oldest sessions are deleted until it is under.

### Failure handling
- **Processing error**: on the first or second failure the video returns to `golf/inbox` with its attempt count incremented.
- **Third failure**: the video moves to `golf/failed/` with `<name>.error.txt`.
- **Run killed mid-video**: the next run moves it back to `golf/inbox` and counts the attempt.
- **Re-upload of a failed video**: the failed copy and its error file are deleted and the new upload starts with a fresh attempt count.

## Risks / Trade-offs
- claude.ai's Drive connector might not hand JPEGs to Claude as images → checked first with a test image, before any pipeline code. If it fails, the fallback is calling the Claude API from the pipeline.
- `latest` with `Always` means a bad push breaks the next run → failures are retried and land in `failed/`, and the original is kept for 3 days.
- Audio onsets at a busy range → pose confirmation plus the eval harness. Rejected candidates in `session.md` make misses visible.
- Full `drive` scope on an unverified personal OAuth app → limited to the owner's account; the refresh token only lives in the homelab SOPS secret.

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
