# golf-etl design

golf-etl turns phone videos of golf swings into per-swing frames that Claude can read. A video in the inbox folder is picked up, each swing is found and sliced, and labeled frames are pushed to a private GitHub repo, `rumstead/golf-sessions`. Coaching happens in claude.ai: add `golf-sessions` with **+ > Add from GitHub** and ask it to review the latest session. The claude.ai GitHub integration reads the full-size images directly.

This repo owns the pipeline. The deployment (namespace, CronJob, volume, SOPS secrets, Argo CD Application) lives in [rumstead/homelab](https://github.com/rumstead/homelab). Setting `GOLF_STORAGE=drive` switches to the alternative mode described under Drive mode.

## Context
Swing videos come from an iPhone on a tripod. Two shapes show up: long range sessions (5 to 20 minutes, many swings, impact sounds from neighboring bays) and short clips with a single swing. Uploads happen from the range, off the home LAN.

The pipeline runs on a LAN-only homelab cluster with no public ingress, on a worker with plenty of spare CPU and no GPU.

Claude reads images up to 2576px on the long edge (about 3.75MP) on current models. The `golf-swing-analysis` skill asks about ball flight before diagnosing, so the pipeline does not collect a metadata sidecar.

## Goals / Non-Goals
- Goals:
  - Get per-swing frames into the sessions repo within a few minutes of a video landing in the inbox, with no other steps
  - Frames clear enough for Claude to judge body positions at address, top, impact, and finish, and to read launch monitor numbers
  - Never process the same video into two sessions, and reprocess by re-sending it
  - Keep the inbox small: originals are deleted once published, failed ones after 3 days. In drive mode, also keep Drive usage bounded on a personal Gmail quota
- Non-Goals:
  - Judging strike location on the clubface. At 120fps slo-mo the contact frame shows up in the impact zoom for wedge-speed swings, but at full driver speed the clubhead still moves about 19cm between frames at 240fps, so it is not guaranteed. Impact tape or foot spray photos are a follow-up
  - Publishing clips or the original video. A swing is found in the original by its time in the video
  - Calling the Claude API, notifications, an MCP server
  - Real-time feedback

## Flow
```
upload --> inbox/ under GOLF_INBOX_ROOT
                       |
     golf-etl poll (every 3 minutes, never overlapping)
       1. recover   anything left in processing/ goes back to inbox/
       2. claim     move the video to processing/
       3. detect    audio onsets, confirmed by fast hands at hip height
       4. slice     impact -2.5s .. +1.5s, re-encoded in scratch for ball tracking
       5. frames    address / top / impact / finish, impact zoom, pose sheet, ball flight
       6. publish   one commit to golf-sessions replaces sessions/<id>/
       7. delete    original deleted from the inbox
       8. sweep     failed originals past their TTL
                       |
claude.ai: + > Add from GitHub > golf-sessions, "review my latest golf session"
```

## Decisions

### Upload and trigger
- **A folder inbox, polled.** The cluster has no public ingress, so nothing can push a notification to it. Polling every 3 minutes keeps a video's wait to about 3 minutes. The inbox is the folder at `GOLF_INBOX_ROOT` (default `/data`); whatever fills it must leave only complete videos in `inbox/`. Alternatives: in-cluster S3 needs a way in from off the LAN; a watcher that spawns a Job per video adds moving parts for one upload per range trip.
- **The inbox is read through the `rclone` binary pointed at a local path**, the same code that reads a Drive remote, so both modes share one `Store` interface (list, move, download, delete, state file). Sessions go through a separate `Publisher` interface: `GitHubPublisher` or, in drive mode, `DrivePublisher`.
- **Sessions go to GitHub over SSH with a deploy key** that has write access to `golf-sessions` only and does not expire. The key is read from `GOLF_SSH_KEY_PATH` (default `/etc/golf-etl/ssh/id_ed25519`). GitHub's host keys are pinned in the package (`github_known_hosts`) and checked strictly, so there is no first-use trust. The repo is `GOLF_SESSIONS_REPO` (default `git@github.com:rumstead/golf-sessions.git`); a `file://` URL needs no key.

### Upload page
- **The phone uploads through a page in the cluster**: nginx in front, tusd for the resumable tus protocol, and `golf-etl upload-server` for the page, a `/status` JSON, and tusd's HTTP hooks. The pod, volume, and tailnet ingress live in homelab.
- **tusd writes to `GOLF_UPLOAD_DIR`** (default `/data/uploads`), on the same volume as the inbox. The `pre-create` hook rejects anything that is not a video. The `post-finish` hook moves the finished file into `inbox/` under its original name, with `-2`, `-3` before the suffix when the name is taken, so `inbox/` only holds complete videos.
- **A sweep every 10 minutes** moves complete uploads whose hook never arrived and deletes unfinished ones untouched for 24 hours (`GOLF_UPLOAD_TTL_HOURS`). Uploads tusd holds a lock on are left alone.
- `upload-server` listens on `127.0.0.1` only, port 8090 (`GOLF_UPLOAD_PORT`), so nginx and tusd are its only clients.
- The page sends each video in 50MB chunks with tus-js-client, one at a time, and resumes an interrupted upload when the same file is picked again. It lists `/status` every 10 seconds: uploads in progress, videos waiting in `inbox/`, and the recent results from `state.json`.
- Videos are picked from Files (Choose File), not Photos: Photos hands Safari a re-encoded H.264 copy with its `creation_time` rewritten to the upload time, while Files hands it the original byte for byte.

### Layout
Inbox, under `GOLF_INBOX_ROOT`:
```
inbox/          uploads land here
processing/     claimed by the running job
failed/         originals that failed 3 attempts, plus <name>.error.txt
.golf-etl/
  state.json    retry counts and the status of recent uploads
```
Sessions, in the `golf-sessions` repo:
```
sessions/
  <session-id>/
    .golf-etl.json
    session.md
    swing-01/
      05-impact-zoom.jpg
      06-sequence.jpg
      07-after-shot.jpg
      ball.json
      pose.csv
      sim.json
```
`sim.json` is written only for sessions where the simulator panel is found. `clip.mp4` and the `-small.jpg` copies are not published. Non-video files in `inbox/` are left alone (strike photos will use them later).

### Identity and idempotency
- **Identity is the SHA-256 of the video.** rclone computes it for a local file, so filenames like `IMG_1234.MOV` repeating across days do not matter. A file without a hash is skipped until the next poll.
- **Session ID** is `<capture time YYYY-MM-DD-HHMM>-<first 8 hex of the hash>`. Capture time comes from the video's `creation_time` (ffprobe), falling back to the file's modified time. Each session folder holds a `.golf-etl.json` marker with `{sha256, sourceName, pipelineVersion, published}`.
- **State** lives in `.golf-etl/state.json` under the inbox root, which is safe with a single writer since runs never overlap. It holds `attempts` (retry counts by hash) and `recent`: the latest status of the last 50 uploads, newest first, each with `name`, `sha256`, `status` (`processing`, `retrying`, `failed`, or `published`), `updated`, and the `session` id or the last line of the `error`. A video appears once, with its latest status.
- **Claiming**: a video is moved from `inbox/` to `processing/` before any work. Runs never overlap and a failed run is not restarted in place (see Runtime contract), so anything in `processing/` when a run starts belongs to a dead run. It is moved back to `inbox/` with its retry count incremented and retried in the same run.
- **Atomic replace**: a publish is one commit that replaces `sessions/<session-id>/`, so a session is never half written. Publishing the same files again makes no commit. The original is deleted only after the push succeeds, and a failure saving state after that does not send a published video back to retry.

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
3. Every onset is a candidate. Transients closer than 0.25s are merged into the stronger one (a sound and its echo), but loudness never decides between swings: a club tap, the finish, and a practice swing can all be louder than the strike a few seconds away.
4. MediaPipe Pose Landmarker (lite) runs on a 0.8s window around each candidate at 640px and at most 60fps, never on the whole video. The largest person in frame is the golfer. A candidate is confirmed when, within 200ms of the onset (`GOLF_CONFIRM_WINDOW_MS`), the hands (midpoint of both wrists, which works for either handedness) move at least 4 leg lengths per second (`GOLF_CONFIRM_MIN_SPEED`), and over the pose samples within 20ms of the onset (at 60 a second, the one either side), or the nearest one when none is that close, they reach or pass through the band from 0.15 leg lengths above to 0.25 below the hips (`GOLF_CONFIRM_HANDS_MIN`, `GOLF_CONFIRM_HANDS_MAX`). With a driver the hands come back up fast after impact, and filmed from behind they can be past the limit by the sample nearest the sound. Leg length is the median hip to ankle height in the window, so a phone close to the golfer does not make a waggle look fast; on five labeled videos from three camera spots the strikes ran 4.8 to 9.9 leg lengths per second. The hand height check rejects fast hands at the top or the finish and waggles at address. Where the hands are fastest overall does not matter: in a 12 minute simulator session, 10 of 21 swings had their fastest hands in the follow-through, 400 to 1300ms after impact.
5. Confirmed candidates closer than 8s (`GOLF_ONSET_MIN_GAP_S`) keep the one with the fastest hands. Videos under 15s keep only the fastest. Only kept swings get pose for the whole clip window (impact minus 2.5s to plus 1.5s) for frame selection. Every other candidate is listed in `session.md` as rejected with the reason.
6. Zero confirmed swings is not a failure. The session is written with `session.md` only.

All thresholds live in a config module with environment variable overrides (`GOLF_ONSET_MIN_GAP_S`, `GOLF_CONFIRM_WINDOW_MS`, and so on) so tuning does not need a rebuild.

### Slicing and frames
- **Clip**: impact minus 2.5s to impact plus 1.5s, re-encoded (stream copy snaps to keyframes) to 1080p long edge, H.264 CRF 23, AAC. It is cut in scratch for ball tracking and discarded with the scratch directory. In drive mode it is also published.
- **Frames are decoded from the original video**, never from the re-encoded clip. HDR video (HLG or PQ, the iPhone default) is tone mapped to SDR BT.709 so frames are not flat and washed out. Portrait rotation is applied.
  - Address: end of the last still stretch (0.2s or more) of the hands before the top
  - Top: highest hands in the 2s before impact
  - Impact: the last frame with the ball still on its rest spot (see ball flight), searched within 12 frames either side of the corrected audio onset. The ball counts as on its spot while the middle of the rest blob is closer to its color before impact than after. Face-on that is the frame where the clubhead meets the ball and the ball is already a streak. Down the line the ball leaves along the camera's line of sight and still covers its spot the frame after contact, so impact is one frame earlier, the clubhead at the ball. On the 2026-10-18 sessions the sound's frame was 0 to 5 frames earlier. Without a rest spot, or when the ball does not leave it in that window, impact is the frame nearest the corrected audio onset. The sequence panels, the Timing table and `pose.csv` all time the swing from this impact
  - Finish: highest hands from 0.3s after impact to the end of the clip window
  - For every position but impact, the sharpest frame (Laplacian variance) within plus or minus 2 frames of the target is used. Impact is never moved.
- **Images**: JPEG quality 90, sRGB, metadata stripped, label and time in the video burned into a corner. Every image fits 2560px on the long edge and 3.7MP (`GOLF_FRAME_MAX_PIXELS`). Full size is kept because shrinking a session to a few tens of MB makes launch monitor numbers on `07-after-shot.jpg` unreliable to read. A 1080p range session is about 0.93MB per swing on GitHub and a detailed simulator session about 2.4MB per swing (about 72MB for 30 swings), so GitHub's 10GB recommendation holds roughly 130 of the largest sessions.
  - `05-impact-zoom.jpg`: a native-resolution band at ball height (centered on the ankles down the line and 0.35 leg lengths below them face-on, a quarter of the frame tall, the frame width up to 1.2 frame heights) for impact minus 1, impact, and impact plus 1, stacked. Sideways the ball sits between the feet face-on but past the toes down the line, so the band covers both instead of guessing
  - `06-sequence.jpg`: the whole swing in one image, two rows of four in reading order: address, takeaway, halfway back, top, transition, impact, follow-through, finish. Takeaway is `GOLF_TAKEAWAY_BEFORE_TOP_S` (default 0.33s) before the top and never before the takeaway start (see the Timing table): on every 2026-10-18 swing, both camera angles, iron and driver, the club was parallel to the ground 0.31 to 0.37s before the top. Halfway back is the first moment the hands reach the shoulder line, or without shoulders in the pose two thirds of the time from address to top. Transition is `GOLF_TRANSITION_S` (default 0.1s) before impact and follow-through `GOLF_FOLLOW_THROUGH_S` (default 0.15s) after it, kept between the top and the finish. Each panel has the pose skeleton and a label drawn after resizing so it stays legible, and the sheet fits 2560px and 3.7MP
- `07-after-shot.jpg`: one full-resolution frame `GOLF_AFTER_SHOT_S` (default 8s) after impact, pulled earlier when the next swing's clip window starts sooner and kept inside the video. On a simulator the screen shows the previous shot until about 2s after impact and this shot's final numbers (carry, total) by about 8s, so this is the only image taken late enough to show this shot's numbers. When the sim panel is read, the numbers come from the Sim table and `sim.json` instead (see Sim readout), and this frame covers blank cells and numbers the panel does not show. On the range it is just the golfer after the shot.
- `pose.csv`: the swing's pose track as numbers, one row per sample (up to 60 a second, the same track the sequence skeletons come from). Columns are the time in the video, milliseconds from impact, the frame number, then x, y and visibility for the nose, shoulders, wrists, hips, knees, and heels. Frame numbers count from 2.5s before impact at the source fps (frame 0 is the start of the clip window). x and y are fractions of the frame width and height from the top left, rounded to 3 decimals, and a sample without a pose leaves its cells empty. A 4s window at 60 samples a second is about 50KB.
- **Times in the video** read like the Photos scrubber: under a minute as seconds (`4.37s`), a minute or more as minutes and seconds (`12:07.4`). Image labels, `session.md`, and the Timing table all use this, so a time Claude names is the time to scrub to in the original video.
- `session.md` lists source name, capture time, hash, pipeline version, each swing with its impact time, confidence and ball flight (speed, start direction, grade, or why the ball was not tracked) under a one-line ball flight summary, rejected candidates, and removed duplicates. A Timing table gives each swing's takeaway start, top, and impact as frame numbers (counted from 2.5s before impact, at the source fps) with their time in the video, plus backswing time (takeaway start to top), downswing time (top to impact), and tempo (backswing over downswing). The takeaway start is the last moment before the hands reach the hips (and are at least 0.1 leg lengths above their median height over the 0.2s before address, so a setup with the hands at the hip line does not reach it on pose jitter) that they were within 0.05 leg lengths of their lowest point at address, on lightly smoothed heights, so pose jitter at address does not start it early. When the wrists' pose visibility drops below 0.5 around the top (the hands left the frame, or there is no pose) the top is reported as not seen and the durations are left blank. When there are swings it also carries a short "How to read this session" section naming each file in the folder, its order, and what it is good for, so any chat that reads it knows what to do without instructions; it describes only the files that were published. When the sim panel is found it also has a `## Sim` table, one row per swing, with the readout fields as columns and a short note on how to read blank cells. A 33 swing session is about 8KB.
- **Drive mode** also writes `06-sequence-small.jpg`, `05-impact-zoom-small.jpg`, and `07-after-shot-small.jpg`: the same images at the largest size and JPEG quality that fits `GOLF_SMALL_MAX_BYTES` (72,000 bytes), because the Drive connector returns file contents as base64 capped at about 100,000 characters per read. Their labels are drawn at the small size. They and `clip.mp4` are never pushed to GitHub, and in drive mode `session.md` names them and `pose.csv` counts frames in `clip.mp4`.
- Recording tip for the README: in daylight, 240fps slo-mo usually helps the impact frames more than 4K does because the faster shutter cuts blur. Both work.

### Ball flight
The ball is the only small object that sits still before impact and leaves its spot fast, so it is found against per-pixel medians without a trained model. It is read off the swing's scratch clip (the clip keeps the source frame rate), from 0.5s before impact to 1.42s after, at 960px on the long edge. Pixel limits below are at that scale and per frame at 120fps, scaled to the clip's frame rate.
1. **Camera check.** When more than 15% of the background outside the golfer (the address pose's extent plus 0.05 frame widths) changes brightness by more than 40 between the first and last quarter second, the camera was bumped or blocked: `lost: camera moved`.
2. **Rest spot.** The address pose's ankles set a band from 0.06 frame heights above them to 0.07 below. The ball at rest is a spot in that band that is ball colored in the median of the frames before impact, gone from the median of the frames after it, and seen going within 12 frames of impact (a yellow spot on the mat that goes away later is not the ball that was hit). Yellow is `(R + G) / 2 - B`; when no yellow ball is found, white is the darkest of the three channels. The spot has to be round, between 0.1 and 0.35 foot lengths across (heel to toe from the pose, so the limit holds at any camera distance), off both feet, and not behind the heels when the toes point sideways in the picture. Face-on the toes point at the camera, so foot length is foreshortened, and the ball sits between the golfer and the phone, half a leg below the ankles in the picture: the band reaches 0.8 leg lengths below the ankles (hip to ankle height at address) and the spot is 0.03 to 0.12 leg lengths across. On the 2026-10-18 face-on session (8 shots, 120fps, portrait) the ball sat 0.50 to 0.57 leg lengths below the ankles and was 0.07 across.
3. **Chain.** In each frame, blobs of 2 to 120px that stand out from the window's median in the ball's color. A chain starts within 40px sideways and 150px above the rest spot, from 4 frames before impact to 10 after, and steps to the nearest blob no lower than the last point, at most 60px higher and 40px sideways, with gaps of up to 4 frames. The golfer's side of a line 0.22 frame widths from the rest spot is masked. The longest chain that rises more than 40px over its first five points wins. Leading points still at the rest spot are dropped except the last.
4. **Metrics** from the first chain point and the tenth after it: `ball_px_per_frame` (distance over frames), `start_dir` (sideways over up, negative is left of the camera line), `impact_frame` (the first point) and `rise_px` (first point to last).
5. **Grade** within the session: good at `GOLF_BALL_GOOD` (0.9) times the median speed of the tracked swings or more, poor under `GOLF_BALL_POOR` (0.78) times it, fair between. Pixels per frame depend on camera distance and frame rate, so speeds are never compared across sessions. Start direction is reported, not graded, until there is a target line in the picture.

A swing that cannot be followed gets a `lost:` reason and no numbers or grade: `no golfer at address`, `clip under 100fps` (at 30fps the ball is out of frame three frames after impact), `camera moved`, `no ball launched` (no ball, or a practice swing next to one), `ball lost after launch`, `too fast to follow face-on` (the ball was hit, but face-on it crosses a 120fps portrait frame in two or three frames, already a streak in the first), or `tracking error` (logged; the rest of the swing is still written). `swing-NN/ball.json` keeps the metrics, the chain points and the thresholds so a later run can re-grade without decoding video. Tracking takes about 1.5s per swing.

On the 2026-10-16 range session (33 clips, 120fps, two camera spots, yellow and white balls) it followed all 26 real shots and gave a lost reason for the 7 that were not shots, and its grades matched the golfer's calls on the six swings he named.

### Sim readout
The simulator draws its numbers in a 2x6 panel at the top right of the screen. The fields, in panel order: club_path, carry_game, face_to_path, carry_raw, back_spin, total_length, smash, ball_speed, peak_height, total_spin, face_to_target, club_speed. The pipeline reads them locally with glyph templates: no model and no export from the simulator.
- **Finding the panel**: a Canny edge reference (`sim_panel.png`) is matched at several scales. Per video, one frame at each of impact + 3, 4, and 5s over the first 5 swings is searched, only when that offset falls inside the swing's shot window. The box wins when at least 3 panels agree within 2px on every edge and they are more than half of the panels found (not of all probed frames). Otherwise there is no Sim output for the video.
- **Reading a cell**: the color distance from the cell's median color is thresholded, the glyphs are cut out, each is matched to its nearest neighbor in `sim_glyphs.npz`, and the result must fit the field's fixed format. Any doubt leaves the cell blank, because a wrong number is worse than a blank.
- **Voting**: each swing's frames are sampled twice a second from impact + 2s to the earliest of impact + 15s, the next swing's clip start, and the video end. A value needs at least 2 votes and twice the runner-up's.
- **Output**: `swing-NN/sim.json` is `{"panel": [x0, x1, y0, y1], "frames": n, "fields": {name: {"text", "value", "votes", "seen_s"}}}`, and `session.md` gets the `## Sim` table. A row of blanks can mean the next swing started before the numbers showed.
- **Rebuilding the glyphs**: hand-label shots in `local/sim_labels.yaml` as `videos: [{path, shots: [{impact, <field>: "text"}]}]`, quoting every value. `carry_game` and `total_length` are not labeled because they count up while the ball flies. Run `golf-etl sim-glyphs local/sim_labels.yaml src/golf_etl/sim_glyphs.npz`, then `golf-etl eval-sim local/sim_labels.yaml`, which holds each video out in turn. Labeled eval windows start at impact + 3s (later than the pipeline's 2s) so a labeler reads the screen once this shot's numbers are up. The current held-out result is 186 of 190 labeled fields right, 0 wrong, 4 blank, over 19 shots in 3 videos (two normal, one 240fps slo-mo).

### Retention
- The original is deleted from the inbox once its session is pushed.
- A sweep at the end of every run deletes `failed/` originals older than 3 days (`GOLF_FAILED_TTL_DAYS`), aged from their `.error.txt` since a video's own time is when it was shot.
- Sessions in `golf-sessions` are never deleted: there is no TTL or size cap. Re-sending a video replaces its session in a new commit.

### Publishing to GitHub
- Each publish starts from an empty work directory under `GOLF_SCRATCH_DIR`: a depth-1, blob-filtered fetch of `main` with only that session checked out. A run never downloads the repo's history however many sessions it holds.
- The session folder is replaced, the marker written, and one commit `publish <session-id>` is pushed to `main` as `golf-etl <golf-etl@localhost>`. `clip.mp4` and `*-small.jpg` are left out.
- The same code can verify a push: `published_files` lists a session's paths and sizes as the remote has them.

### Drive mode
`GOLF_STORAGE=drive` keeps the Drive layout: the inbox, `processing/`, `failed/`, the state file, and `sessions/` all live in the Drive folder `GOLF_REMOTE` (default `gdrive:golf`). Everything else in this document applies, with these differences:
- Identity is Drive's `sha256Checksum`, falling back to `md5Checksum`, so no download is needed. A file with neither yet is skipped until the next poll.
- Access is through rclone with its own verified OAuth app and the full `drive` scope. `RCLONE_CONFIG_GDRIVE_TYPE=drive`, `RCLONE_CONFIG_GDRIVE_SCOPE=drive`, `RCLONE_CONFIG_GDRIVE_TOKEN` (the JSON from `rclone authorize "drive"`) and `RCLONE_DRIVE_USE_TRASH=false` are set in the environment.
- A session is written to `sessions/.tmp-<id>/`, any existing `sessions/<id>/` is permanently deleted, and the temp folder is renamed into place.
- Each swing also has `clip.mp4` and the three `-small.jpg` copies.
- Originals are removed with `files.delete` (permanent), since trashed files still count against quota.
- The sweep also enforces the Drive budget:
  - `clip.mp4` files older than 14 days are permanently deleted (`GOLF_CLIP_TTL_DAYS`)
  - session folders older than 60 days are permanently deleted (`GOLF_SESSION_TTL_DAYS`)
  - if session files still total over 3GiB (`GOLF_MAX_BYTES`), the oldest sessions are deleted until they are under; failed originals do not count, since they expire in 3 days anyway
  - the sweep is one recursive listing of `sessions/`: a clip is any `clip.mp4`, a session's age is its marker's time, and leftover `.tmp-` and `.old-` folders from an interrupted swap are cleaned up or restored
- Rough sizes: a 30-swing session is about 200MB on day one and about 120MB once clips expire.

### Failures
- Each video is processed inside its own try block. On an exception `attempts` is incremented and the video goes back to `inbox/`. On the third failure it moves to `failed/` with `<name>.error.txt` holding the exception and the last log lines.
- Crashes the process cannot catch (OOMKill, a deadline kill) are handled by the recovery step at the start of the next run.

## Runtime contract
Whatever schedules `golf-etl poll` must guarantee:
- Runs never overlap (`concurrencyPolicy: Forbid`)
- A failed run is not restarted in place (`restartPolicy: Never`, `backoffLimit: 0`), so recovery at startup is safe
- The inbox folder is mounted at `GOLF_INBOX_ROOT` and writable by uid 10001
- The deploy key is mounted at `GOLF_SSH_KEY_PATH`; the pipeline exits with status 2 and a message when it is missing
- About 10Gi of scratch disk at `GOLF_SCRATCH_DIR` (4K/60 is about 400MB per minute) and up to 3Gi of memory
- Any threshold or TTL overrides as `GOLF_<NAME>` variables
- In drive mode, the `RCLONE_CONFIG_GDRIVE_*` variables above instead of the deploy key (exit status 2 without the token)

`GOLF_STORAGE` must be `github` (the default) or `drive`; anything else exits with status 2. `poll-drive` is an alias of `poll`.

## Repo
- Python 3.12, `requirements.txt` with exact pins, Dockerfile on `python:3.12-slim` with ffmpeg, git, openssh-client, rclone, and the bundled Pose Landmarker lite model.
- CLI:
  - `golf-etl process <video> --out <dir>`: local run, no inbox or GitHub
  - `golf-etl eval <labels.yaml>`: detection precision, recall, and timing error against hand-labeled impact timestamps
  - `golf-etl poll`: the scheduled entrypoint (alias `poll-drive`)
  - `golf-etl upload-server`: the upload page, its status, and tusd's hooks
  - `golf-etl migrate-drive [--dry-run]`: one-time move of the sessions in Drive to `golf-sessions`. It exits with status 2 without both the Drive token and the deploy key, and refuses (status 1, listing the entries) while anything is in the Drive `inbox/`, `processing/`, or `failed/`. Each session is copied without `clip.mp4`, `-small.jpg` copies, or `session.zip`, keeping its layout, then deleted from Drive only if the pushed commit has every file at the same size. A session with no marker is kept on Drive. The exit status is 1 when any session was kept
- Tests:
  - onset detection against synthetic audio (clicks plus noise)
  - frame selection against canned pose sequences
  - every row of the idempotency table and every failure scenario against a fake store (`tests/fake_store.py`); the inbox and Drive layer sit behind the small `Store` interface for this
  - the GitHub publisher against a local bare repo reached by `file://`
  - synthetic videos generated with ffmpeg inside the tests (test pattern, clicks, HDR, rotation)
  - real footage kept out of git in `local/` with a `local/labels.yaml` of hand-labeled impact times; `pytest -m real` runs eval and full processing on it and skips when it is missing
- GitHub Actions runs tests and pushes `ghcr.io/rumstead/golf-etl:latest` on `main`. The cluster pulls `latest` with `imagePullPolicy: Always`, so there are no version tags to bump.
- Renovate (`renovate.json5`): `config:recommended` with the `pip_requirements`, `dockerfile`, and `github-actions` managers, Monday before 6am America/New_York like homelab, with `mediapipe`, `opencv-*`, and `numpy` grouped since their versions are coupled.

## Requirements
These are the behaviors the tests pin down.

### Inbox ingestion
The pipeline polls `inbox/` and turns each video into a session in `golf-sessions`.
- **Video in the inbox**: processing starts on the next poll and the session is pushed when it completes.
- **Non-video file**: a file that is not a video is left in place unchanged.

### Content-based idempotency
A video is identified by its checksum, not its filename. One video maps to at most one session folder.
- **Duplicate copies in one poll**: exactly one is processed, the others are deleted and listed in `session.md`.
- **Re-upload after success**: the video is reprocessed and the existing session folder replaced in a new commit. No second folder is created.
- **Same filename, different video**: each produces its own session.
- **Failed push**: the session already in the repo is untouched and the video is retried.

### Swing detection
Swings are found by audio onsets confirmed by fast hands at hip height at the sound.
- **Range session with neighboring bays**: each of the owner's swings is emitted separately, and unconfirmed onsets are listed in `session.md` as rejected with timestamps.
- **Impact timing**: the impact frame is within two frames of the first frame showing the clubface on the ball, on 120fps footage.
- **Louder sound nearby**: a club tap, waggle, or finish louder than the strike within 8s does not hide the strike.
- **Phone distance**: hand speed is measured in leg lengths, so the same swing confirms the same way filmed close or far.
- **Single-swing clip**: a video under 15 seconds emits at most one swing.
- **No swing found**: the session is written with `session.md` stating zero swings, and it is not a failure.

### Swing frames for Claude
Each swing gets labeled JPEGs decoded from the original video, 2560px long edge or smaller and never upscaled.
- **Detected swing**: the folder contains `pose.csv`, `ball.json`, `05-impact-zoom.jpg`, `06-sequence.jpg`, and `07-after-shot.jpg`, each JPEG sRGB and labeled with position and time in the video. It has no `clip.mp4` or `-small.jpg` copies.
- **Sim readout**: when the panel is found, each swing also has `sim.json` and `session.md` has a `## Sim` table. Cells that cannot be read confidently are blank, never guessed.
- **Drive mode**: each swing also has `clip.mp4` and the `-small.jpg` copies under 72,000 bytes.
- **Impact zoom**: `05-impact-zoom.jpg` shows a native-resolution band at ball height for the frames before, at, and after impact, with the ball in it from either camera angle.
- **iPhone footage**: HDR, portrait, APAC-plus-AAC `.mov` files process like any other video, with natural color.
- **Blurry target frame**: for address, top, and finish, a sharper frame within two frames is used instead.
- **Long videos**: times of a minute or more read as minutes and seconds.

### Ball flight grade
Each swing's ball flight is graded against the session in `session.md`, so a review can rank swings from text alone.
- **Tracked swing**: the swing's row has ball px/frame, start direction, a grade and `ok`, and `ball.json` has the chain points.
- **Yellow dot test**: a synthetic clip with a ball launched at a known speed returns that speed within 5%.
- **Untracked swing**: the row has a `lost:` reason and empty metric cells, never a guessed grade.
- **Size**: `session.md` stays under 50KB so it reads in one call.

### Retention
- **Original after success**: deleted from the inbox.
- **Failed originals**: older than 3 days they are deleted, honoring the override.
- **Sessions on GitHub**: never swept.
- **Drive mode**: clips older than 14 days and sessions older than 60 days are permanently deleted, and if session files total over 3GiB the oldest sessions are deleted until they are under. Failed originals never evict sessions.

### Failure handling
- **Processing error**: on the first or second failure the video returns to `inbox/` with its attempt count incremented.
- **Third failure**: the video moves to `failed/` with `<name>.error.txt`.
- **Run killed mid-video**: the next run moves it back to `inbox/` and counts the attempt.
- **Re-upload of a failed video**: the failed copy and its error file are deleted and the new upload starts with a fresh attempt count.
- **Status**: `state.json` has each recent upload's latest status; nothing about failures is pushed to `golf-sessions`.

## Coaching skill
`claude/skills/golf-swing-analysis/SKILL.md` is the owner's claude.ai skill. It triggers on requests like "review my latest golf session", finds the newest session folder in the `golf-sessions` repo (names sort by capture time), reads `session.md`, skips swings with no ball launched, picks up to 5 swings by ball flight grade (or spread across the session without grades) unless told otherwise, views each swing's sequence sheet and impact zoom, reads simulator numbers from the Sim table and `sim.json` first, then from `07-after-shot` for blank cells and numbers the panel does not show (and never from earlier frames, which show the previous shot), uses the Timing table and `pose.csv` for measurements, asks about ball flight shape once per session when there are no numbers, and names the source video and the Photos scrub time for every swing it mentions, then runs the existing coaching steps. It is uploaded to claude.ai by hand; claude.ai's copy is the one that runs.

## Risks / Trade-offs
- `latest` with `Always` means a bad push breaks the next run → failures are retried and land in `failed/`, and the original is kept for 3 days.
- Audio onsets at a busy range → pose confirmation plus the eval harness. Rejected candidates in `session.md` make misses visible.
- The sessions repo only grows → a session is about 0.93 to 2.4MB per swing, and GitHub recommends repos under 10GB.
- The deploy key can write to `golf-sessions` → it has no other access, and it does not expire.
- Drive mode relies on rclone's shared OAuth app and full `drive` scope → the token only reaches the owner's account; rclone's app shares Google API quota with other rclone users, which a few calls every 3 minutes does not approach.

## Follow-ups
- The upload page that fills the inbox
- Strike photos: impact tape or foot spray photos in the inbox, attached to the session recorded just before them
- Notification when a session is ready
- Claude API analysis and an MCP server for sessions
