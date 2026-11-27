# golf-etl design

golf-etl turns phone videos of golf swings into per-swing frames that Claude can read. A video in the inbox folder is picked up, each swing is found and sliced, and labeled frames are pushed to a private GitHub repo, `rumstead/golf-sessions`. Coaching happens in claude.ai: add `golf-sessions` with **+ > Add from GitHub** and ask it to review the latest session. The claude.ai GitHub integration reads the full-size images directly.

This repo owns the pipeline. The deployment (namespace, CronJob, volume, SOPS secrets, Argo CD Application) lives in [rumstead/homelab](https://github.com/rumstead/homelab).

## Context
Swing videos come from an iPhone on a tripod. Two shapes show up: long range sessions (5 to 20 minutes, many swings, impact sounds from neighboring bays) and short clips with a single swing. Uploads happen from the range, off the home LAN.

The pipeline runs on a LAN-only homelab cluster with no public ingress, on a worker with plenty of spare CPU and no GPU.

Claude reads images up to 2576px on the long edge (about 3.75MP) on current models. The `golf-swing-analysis` skill asks about ball flight before diagnosing, so the pipeline does not collect a metadata sidecar.

## Goals / Non-Goals
- Goals:
  - Get per-swing frames into the sessions repo within a few minutes of a video landing in the inbox, with no other steps
  - Frames clear enough for Claude to judge body positions at address, top, impact, and finish, and to read launch monitor numbers
  - Never process the same video into two sessions, and reprocess by re-sending it
  - Keep the inbox small: originals are deleted once published, failed ones after 3 days
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
- **The inbox sits behind a `Store` interface** (list, move, download, delete, state file) that `LocalStore` implements on the folder. Sessions go through a separate `Publisher` interface, implemented by `GitHubPublisher`.
- **Sessions go to GitHub over SSH with a deploy key** that has write access to `golf-sessions` only and does not expire. The key is read from `GOLF_SSH_KEY_PATH` (default `/etc/golf-etl/ssh/id_ed25519`). GitHub's host keys are pinned in the package (`github_known_hosts`) and checked strictly, so there is no first-use trust. The repo is `GOLF_SESSIONS_REPO` (default `git@github.com:rumstead/golf-sessions.git`); a `file://` URL needs no key.

### Upload page
- **The phone uploads through a page in the cluster**: nginx in front, tusd for the resumable tus protocol, and `golf-etl upload-server` for the page, a `/status` JSON, and tusd's HTTP hooks. The pod, volume, and tailnet ingress live in homelab.
- **tusd writes to `GOLF_UPLOAD_DIR`** (default `/data/uploads`), on the same volume as the inbox. The `pre-create` hook rejects anything that is not a video, and a club the page does not offer. The `post-finish` hook moves the finished file into `inbox/` under its original name, with `-2`, `-3` before the suffix when the name is taken, so `inbox/` only holds complete videos.
- **The club rides in the file name.** The page has a club picker (7 iron, the default, driver, or mixed for a session of several clubs) and sends the choice as tus metadata. A driver or mixed session lands in `inbox/` as `IMG_4881.mixed.MOV`, so the club follows the video through every move the poller makes. The poller takes the tag back off: the session's source name is `IMG_4881.MOV`, and `session.md` gets a `Club:` line and `summary.json` a `club` field. A name without a tag, such as a file copied straight into `inbox/`, is a 7 iron.
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
  index.md
  chain.md
  takeaway-labels.csv   (the golfer's, read only)
  <session-id>/
    .golf-etl.json
    session.md
    summary.json
    swing-01/
      05-impact-zoom.jpg
      06-sequence.jpg
      07-after-shot.jpg
      ball.json
      pose.csv
      sim.json
```
`sim.json` is written only for sessions where the simulator panel is found. `clip.mp4` is not published. Non-video files in `inbox/` are left alone (strike photos will use them later).

### Identity and idempotency
- **Identity is the SHA-256 of the video.** The inbox listing computes it for each file, so filenames like `IMG_1234.MOV` repeating across days do not matter. A file without a hash is skipped until the next poll.
- **Session ID** is `<capture time YYYY-MM-DD-HHMM>-<first 8 hex of the hash>`. Capture time comes from the video's `creation_time` (ffprobe), falling back to the file's modified time. Each session folder holds a `.golf-etl.json` marker with `{sha256, sourceName, pipelineVersion, published, venue}`.
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
5. Confirmed candidates closer than 8s (`GOLF_ONSET_MIN_GAP_S`) to each other are a span. When the video has a sim readout panel, every candidate in a span whose screen changed after it is kept: the fields read 1.5s to 0.25s before the sound against those read 2 to 5s after it, at least 4 fields read on both sides and at most half equal, and a change the screen already showed for an earlier candidate is not credited again, which keeps out a practice swing whose before-window straddles the screen update. The rest of the span, and every span on a video with no panel, keeps the one with the fastest hands. Videos under 15s keep only the fastest. Only kept swings get pose for the whole clip window (impact minus 2.5s to plus 1.5s) for frame selection. Every other candidate is listed in `session.md` as rejected with the reason (`within 8s of a swing that changed the screen`, `no screen change, within 8s of a faster swing`, or `within 8s of a faster swing` when the screen could not be read).
6. Zero confirmed swings is not a failure. The session is written with `session.md` only.

All thresholds live in a config module with environment variable overrides (`GOLF_ONSET_MIN_GAP_S`, `GOLF_CONFIRM_WINDOW_MS`, and so on) so tuning does not need a rebuild.

### Slicing and frames
- **Clip**: impact minus 2.5s to impact plus 1.5s, re-encoded (stream copy snaps to keyframes) to 1080p long edge, H.264 CRF 23, AAC. It is cut in scratch for ball tracking and discarded with the scratch directory.
- **Frames are decoded from the original video**, never from the re-encoded clip. HDR video (HLG or PQ, the iPhone default) is tone mapped to SDR BT.709 so frames are not flat and washed out. Portrait rotation is applied.
  - Address: end of the last still stretch (0.2s or more) of the hands before the top
  - Top: highest hands in the 2s before impact
  - Impact: the last frame with the ball still on its rest spot (see ball flight), searched within 12 frames either side of the corrected audio onset. The ball counts as on its spot while the middle of the rest blob is closer to its color before impact than after. Face-on that is the frame where the clubhead meets the ball and the ball is already a streak. Down the line the ball leaves along the camera's line of sight and still covers its spot the frame after contact, so impact is one frame earlier, the clubhead at the ball. On the 2026-10-18 sessions the sound's frame was 0 to 5 frames earlier. Without a rest spot, or when the ball does not leave it in that window, impact is the frame nearest the corrected audio onset. The sequence panels, the Timing table and `pose.csv` all time the swing from this impact
  - Finish: highest hands from 0.3s after impact to the end of the clip window
  - For every position but impact, the sharpest frame (Laplacian variance) within plus or minus 2 frames of the target is used. Impact is never moved.
- **Images**: JPEG quality 90, sRGB, metadata stripped, label and time in the video burned into a corner. Every image fits 2560px on the long edge and 3.7MP (`GOLF_FRAME_MAX_PIXELS`). Full size is kept because shrinking a session to a few tens of MB makes launch monitor numbers on `07-after-shot.jpg` unreliable to read. A 1080p range session is about 0.93MB per swing on GitHub and a detailed simulator session about 2.4MB per swing (about 72MB for 30 swings), so GitHub's 10GB recommendation holds roughly 130 of the largest sessions.
  - `05-impact-zoom.jpg`: a native-resolution band at ball height (centered on the ankles down the line and 0.35 leg lengths below them face-on, a quarter of the frame tall, the frame width up to 1.2 frame heights) for impact minus 1, impact, and impact plus 1, stacked. Sideways the ball sits between the feet face-on but past the toes down the line, so the band covers both instead of guessing
  - `06-sequence.jpg`: the whole swing in one image, two rows of four in reading order: address, takeaway, halfway back, top, transition, impact, follow-through, finish. Takeaway is the first moment going back that the hands reach the hip line (raised 0.1 leg lengths above the address hand height, so a golfer whose hands address at the hips still gets a takeaway off address), which lines it up with halfway back across swings. Without hips in the pose, or when the hands never reach the line, it is `GOLF_TAKEAWAY_BEFORE_TOP_S` (default 0.33s) before the top, and it is never before the takeaway start (see the Timing table). Halfway back is the first moment the hands reach the shoulder line, or without shoulders in the pose two thirds of the time from address to top. Transition is `GOLF_TRANSITION_S` (default 0.1s) before impact and follow-through `GOLF_FOLLOW_THROUGH_S` (default 0.15s) after it, kept between the top and the finish. Every panel of a swing is the same crop of the frame around the golfer, the club and the ball, so the golfer fills the panel instead of sharing it with the room. The crop starts from the shoulders, hands, hips and ankles of every pose sample from 0.2s before address to 0.2s after the finish, and leaves room in leg lengths for what the pose does not have: 0.5 over the shoulders for the head, 0.9 over the hands for the club at the top, 0.2 under the ankles, and to the sides 0.6 down the line or 1.2 beyond the hands face-on, where the club swings across the picture. Down the line it reaches 0.3 past the ball's rest spot, or 1.3 past the golfer on the toe side when no rest spot was found. Without a pose it is the whole frame. Panels are never upscaled, each has a label drawn after resizing so it stays legible, and the sheet fits 2560px and 3.7MP. The pose is not drawn on the panels: it covers the hands and the club, and `pose.csv` and the Body table carry it as numbers
- `07-after-shot.jpg`: one full-resolution frame `GOLF_AFTER_SHOT_S` (default 13s) after impact, pulled earlier when the next swing's clip window starts sooner and kept inside the video. On a simulator the screen switches from the previous shot's numbers to this shot's about 0.2s after impact (0.5s in a slo-mo video's time), and carry and total keep counting up for about 13s, so a frame pulled earlier can show them short of their final values. The other images are all taken within 1.5s of impact, around that switch, so this is the only one that reliably shows this shot's numbers. When the sim panel is read, the numbers come from the Sim table and `sim.json` instead (see Sim readout), and this frame covers blank cells and numbers the panel does not show. On the range it is just the golfer after the shot.
- `pose.csv`: the swing's pose track as numbers, one row per sample (up to 60 a second, the same track the positions and the Body table come from). Columns are the time in the video, milliseconds from impact, the frame number, then x, y and visibility for the nose, shoulders, elbows, wrists, hips, knees, and heels. Frame numbers count from 2.5s before impact at the source fps (frame 0 is the start of the clip window). x and y are fractions of the frame width and height from the top left, rounded to 3 decimals, and a sample without a pose leaves its cells empty. A 4s window at 60 samples a second is about 60KB.
- **Times in the video** read like the Photos scrubber: under a minute as seconds (`4.37s`), a minute or more as minutes and seconds (`12:07.4`). Image labels, `session.md`, and the Timing table all use this, so a time Claude names is the time to scrub to in the original video.
- `session.md` lists source name, capture time, hash, pipeline version, the venue (`sim` when the readout panel was found, otherwise `range`; camera setups differ between the two, so comparisons stay within a venue), the club, each swing with its impact time, confidence and ball flight (speed, start direction, grade, or why the ball was not tracked) under a one-line ball flight summary, rejected candidates, and removed duplicates. A Timing table gives each swing's takeaway start, top, and impact as frame numbers (counted from 2.5s before impact, at the source fps) with their time in the video, plus backswing time (takeaway start to top), downswing time (top to impact), and tempo (backswing over downswing). The takeaway start is the club leaving the ball: the last frame before the patch around the ball's rest spot (8 ball radii to each side, 6 above and 4 below, read at 960px) has changed a fifth of the way from how it looks at address (its median from 0.6s to 0.15s before address) to its biggest change before the hands reach the hips. On the 2026-11-14 sim session the clubhead leaves the ball 0.23 to 0.27s before the hands rise, because down the line the hands first move toward the phone. The club start is not used when there is no rest spot, when the patch is not still at address (a waggle, or the club set down late), when the club leaving changes the patch less than 3 times what it changes at address, or when the change is more than 0.3s from address. The row then says `hands` after the takeaway start, which is the last moment before the hands reach the hips (and are at least 0.1 leg lengths above their median height over the 0.2s before address, so a setup with the hands at the hip line does not reach it on pose jitter) that they were within 0.05 leg lengths of their lowest point at address, on lightly smoothed heights, so pose jitter at address does not start it early. When the wrists' pose visibility drops below 0.5 around the top (the hands left the frame, or there is no pose) the top is reported as not seen and the durations are left blank. When there are swings it also carries a short "How to read this session" section naming each file in the folder, its order, and what it is good for, so any chat that reads it knows what to do without instructions; it describes only the files that were published. When the sim panel is found it also has a `## Sim` table, one row per swing, with the readout fields as columns and a short note on how to read blank cells. A `## Body` table measures the pose at the top and at impact against address, in leg lengths so it does not depend on where the phone is: how far the head moved sideways and up at the top and at impact, how far the hips moved sideways at impact, how far the hands moved sideways and up at impact, how far the hands are over the shoulders at the top, and the spine's lean (hips to shoulders, from vertical) at address and impact. Address is the median pose over the still stretch that ends when the takeaway starts, because the shoulders start turning with the hands and the spine angle can drop 4 degrees in the last 0.2s of it. The top and impact are the median of the pose samples either side of them. A joint with visibility under 0.5 leaves its cells blank. View is `down the line` when the feet point across the picture and `face-on` when they point at the phone. Down the line, side is toward the ball; face-on it is to the right of the picture. When the swings have more than one grade, a `## By grade` table gives the mean of every Sim, Timing and Body number over the swings of each grade (path positive in-to-out, faces positive open), so a number that does not differ between good and poor swings is seen not to. Backswing, downswing and tempo there use only the swings timed off the club when any were. A 33 swing session is about 12KB.
- `summary.json` is the session's row for the index: source, capture time, venue, swing count, grade counts and basis, and the median of each Sim, Timing and Body number over the swings that have it.
- `sessions/index.md` has one row per session, oldest first: venue, swings, grade counts, and the medians of club speed, ball speed, smash, raw carry, peak height, path, face to path, face to target, side spin, launch and tempo. The publisher rebuilds it from every session's `summary.json` on each publish. A session without one has a row with the venue from its marker.
- `summary.json` also has `per_swing`: each swing's grade, camera view, and every Sim, Timing, Body and Strike number session.md has for it.
- `sessions/chain.md` pools every published down-the-line swing of one club (a session without a `club` is a 7 iron; mixed sessions stay out) to say which faults travel with strike quality. Each number is the swing's value minus its session's median, so camera position and day drop out, and a session needs 4 values of a measure for it to count. Spine loss is `spine_address` minus `spine_impact`. The first table gives each measure's rank correlation (Spearman) with ball speed (sim swings only) and with strike, with its swing count and how many sessions it came from. It starts with the golfer's takeaway and halfway-back labels (1 for a level or set club, 0 for a hanging one, taken as they are) and swing order (the swing's place in its session, so warm-up shows up when ball speed climbs with it), then Spine loss with Hips impact side beside it, because some of the spine angle's change is the shoulders turning. The second table counts swings by takeaway, posture kept or lost, and strike near or off. The takeaway is the golfer's by-eye label for the swing's takeaway panel in `sessions/takeaway-labels.csv` (columns `session, swing, label, side, by, date`, and an optional `position`: `takeaway` when blank, or `halfway` for the halfway-back panel; label `hanging`, `level`, `set` or `unreadable`, side optional), which the pipeline only reads: hanging against level or set, with unreadable and unlabeled swings in their own rows, never counted as hanging. A later row for a swing wins, and rows that cannot be used are named in the file. Posture is lost when spine loss is over the pooled median, and strike is measured from the median strike of the session's good swings (from the session's median when it has fewer than two), off when it is farther than the pooled median distance, with the mean signed distance per row. A count under 10 swings is marked too few to read. Each club then repeats both tables for sessions from 2026-10-31 on. The publisher rebuilds it with the index.
- Recording tip for the README: in daylight, 240fps slo-mo usually helps the impact frames more than 4K does because the faster shutter cuts blur. Both work.

### Shaft angle (spike)
A one-day look at whether the shaft angle can be fitted on the sequence frames, run on the 2026-11-08 sim session (IMG_4859, behind-the-golfer camera, 15 swings) at the takeaway, halfway back and top frames rendered without the skeleton overlay. The fit was Canny edges, probabilistic Hough segments at least 0.2 leg lengths long, and the longest segment within a quarter leg length of the wrist midpoint that points away from the hips, excluding exactly vertical and horizontal lines (the screen's borders sit a degree or two off both in the picture, so they pass). The angle to each forearm (elbow to wrist) was computed from the same segment and means nothing unless the segment is the shaft. The frames in `docs/shaft-spike/` show the chosen segment in red with its angle to the horizontal. Every chosen segment was checked by eye at halfway back; at the takeaway and the top a sample was.

- **Halfway back** is the position where the shaft can be fitted from this camera: it is light against the screen and runs from the hands toward the top of the frame. Of 15 swings, the segment is on the shaft on 3 (swings 10, 11 and 15, and on 15 it runs past the grip onto the glove), beside the shaft on the hands or glove on 2 (swings 3 and 9), on a screen tile border a hand's width from the grip on 8, and nothing was chosen on 2. `swing-10-halfway.jpg` and `swing-11-halfway.jpg` are fits, `swing-09-halfway.jpg` the segment beside the shaft, `swing-05-halfway.jpg` a tile border with the shaft visible next to it.
- **Takeaway**: the club is low and the shaft points back toward the camera, mostly hidden by the hands and body. A segment was chosen on 8 of 15; of 4 checked, one is on the shaft where the clubhead is still in view (`swing-05-takeaway.jpg`), the others are a sleeve or a leg edge (`swing-13-takeaway.jpg`).
- **Top**: the club is out of frame on most swings. A segment was chosen on 13 of 15; 12 lie at the same angle as the screen's top border, which the two checked are (`swing-08-top.jpg`), and the other runs along the arm.

What breaks the fit is the screen itself: its tile borders and frame are long straight edges within a hand's reach of the grip, and nearest-long-line selection has no reason to prefer the shaft. The screen is static, so a per-pixel median over the frames around the position, as the ball tracker already does, would remove those edges before the Hough step. A 5 degree bar looks reachable at halfway back, judged from the two frames where the segment and the shaft agree, but only with a selector that uses what the shaft is rather than where it is: a light, thin, continuous line that starts at the grip and keeps its direction across the frames around the position. At the takeaway it needs a face-on or down-the-line camera. No shaft number is written to any output.

### Clubhead at the takeaway (spike)
A one-day look at whether the clubhead can be found when the hands reach hip height going back, as a position against the hands rather than a shaft angle, run on the 2026-11-14 and 2026-11-08 sim sessions (IMG_4877, 5 swings; IMG_4859, 15). The clubhead is the moving part farthest from the hands: pixels that differ from the median of the frames at address and from the median of the frames from the top to the finish, and from the frames 4 either side (a three-frame difference, which drops the legs and the club's spot at address), with the arms masked from the pose, within 1.3 leg lengths of the wrists, and joined to them.

The reference is the owner's by-eye label of each takeaway panel (hanging, level or set) from one pass that was not blind to the detector's results, not ground truth. The overlays (in `local/hinge/` on the machine the spike ran on) are the record: where a check of them disagrees with these labels, the overlay wins.

- **Hanging club, 15 swings** (all 5 IMG_4877 swings, and IMG_4859 01 to 05, 08, 09, 11, 12 and 14): found on 14, at 0.35 to 0.78 leg lengths under the hands. Swing 14 is a miss on a club that was in view: it hangs to about the knee, and the farthest moving point is on the hands instead.
- **Level or set club, 5 swings** (IMG_4859 06, 07, 10, 13 and 15): none read right. Seen from behind the golfer, the club points back at the camera, foreshortened over the hands and the dark top. On 06, 07, 13 and 15 the farthest moving point is on the hands, 0.22 to 0.27 under them, which looks like a middle reading.
- **A wrong height on a club that was found.** On swing 10 the shaft was found, but it runs up across the chest well above the hands, so the true value is above them, and it read 0.09 under. Unlike a miss, nothing flags it. A torso mask hides that set club entirely.
- 14 of 20 swings read right, under the 80% go bar, and the misses fall on the less hanging swings the measure is for, so no takeaway clubhead number is written to any output. The takeaway comes from the golfer's labels in `sessions/takeaway-labels.csv` instead. A camera face-on or square down the line sees a set club against the background.

### Ball flight
The ball is the only small object that sits still before impact and leaves its spot fast, so it is found against per-pixel medians without a trained model. It is read off the swing's scratch clip (the clip keeps the source frame rate), from 0.5s before impact to 1.42s after, at 960px on the long edge. Pixel limits below are at that scale and per frame at 120fps, scaled to the clip's frame rate.
1. **Camera check.** When more than 15% of the background outside the golfer (the address pose's extent plus 0.05 frame widths) changes brightness by more than 40 between the first and last quarter second, the camera was bumped or blocked: `lost: camera moved`.
2. **Rest spot.** The address pose's ankles set a band from 0.06 frame heights above them to 0.07 below. The ball at rest is a spot in that band that is ball colored in the median of the frames before impact, gone from the median of the frames after it, and seen going within 12 frames of impact (a yellow spot on the mat that goes away later is not the ball that was hit). Yellow is `(R + G) / 2 - B`; when no yellow ball is found, white is the darkest of the three channels. The spot has to be round, between 0.1 and 0.35 foot lengths across (heel to toe from the pose, so the limit holds at any camera distance), off both feet, and not behind the heels when the toes point sideways in the picture. Face-on the toes point at the camera, so foot length is foreshortened, and the ball sits between the golfer and the phone, half a leg below the ankles in the picture: the band reaches 0.8 leg lengths below the ankles (hip to ankle height at address) and the spot is 0.03 to 0.12 leg lengths across. On the 2026-10-18 face-on session (8 shots, 120fps, portrait) the ball sat 0.50 to 0.57 leg lengths below the ankles and was 0.07 across.
3. **Chain.** In each frame, blobs of 2 to 120px that stand out from the window's median in the ball's color. A chain starts within 40px sideways and 150px above the rest spot, from 4 frames before impact to 10 after, and steps to the nearest blob no lower than the last point, at most 60px higher and 40px sideways, with gaps of up to 4 frames. The golfer's side of a line 0.22 frame widths from the rest spot is masked. The longest chain that rises more than 40px over its first five points wins. Leading points still at the rest spot are dropped except the last.
4. **Metrics** from the first chain point and the tenth after it: `ball_px_per_frame` (distance over frames), `start_dir` (sideways over up, negative is left of the camera line), `impact_frame` (the first point) and `rise_px` (first point to last).
5. **Grade** within the session: good at `GOLF_BALL_GOOD` (0.9) times the median speed of the tracked swings or more, poor under `GOLF_BALL_POOR` (0.78) times it, fair between. Pixels per frame depend on camera distance and frame rate, so speeds are never compared across sessions. On a sim session with at least 3 swings whose smash factor was read (`GOLF_SMASH_MIN_READS`), every grade comes from smash against the session median instead: good at `GOLF_SMASH_GOOD` (0.97) times the median or more, poor under `GOLF_SMASH_POOR` (0.92). A swing without a smash read has no grade, `ball.json` names the basis in `params.basis`, and the summary line says which one was used. On the 2026-11-08 sim session the two strikes the owner called poor graded poor and the last seven graded good. Start direction is reported, not graded, until there is a target line in the picture.

A swing that cannot be followed gets a `lost:` reason and no numbers, and no grade on the px basis (on the smash basis its grade still comes from the sim): `no golfer at address`, `clip under 100fps` (at 30fps the ball is out of frame three frames after impact), `camera moved`, `no ball launched` (no ball, or a practice swing next to one), `ball lost after launch`, `too fast to follow face-on` (the ball was hit, but face-on it crosses a 120fps portrait frame in two or three frames, already a streak in the first), or `tracking error` (logged; the rest of the swing is still written). `swing-NN/ball.json` keeps the metrics, the chain points, the thresholds and the grade basis so a later run can re-grade without decoding video (on the smash basis together with the smash in `sim.json`). Tracking takes about 1.5s per swing.

On the 2026-10-16 range session (33 clips, 120fps, two camera spots, yellow and white balls) it followed all 26 real shots and gave a lost reason for the 7 that were not shots, and its grades matched the golfer's calls on the six swings he named.

### Strike
Where along the face the ball was struck, read from the impact zoom's frames down the line (face-on the camera looks along the face, so there is no strike).
1. **Contact frame**: of the impact frame and the one after it, the later in which the ball is still on its spot; never an earlier frame. A ball is still while the middle of it (0.4 of its radius, which the club touching the ball's edge does not reach) changes by a mean of 6 gray levels or less against the frame 0.15s before impact. Still balls change 1 to 5, a ball on its way 8 or more. On 36 swings of four sim sessions checked by eye, contact was the impact frame or the one after it every time; a frame before impact has the club short of the ball, and a strike read there sits too far toward the toe. When the club covers the middle of the ball in both frames, there is no contact frame and no strike. Down the line the ball still covers its spot the frame after contact, so the Timing table's impact is timed a frame early on purpose and keeps that.
2. **Hosel**: the mat is plain, so in the contact frame the shaft is a light line on it. Pixels of saturation under 70 and value over 110 within 14 ball radii to the golfer's side of the ball, 6 to the far side, 12 above and 2 below, with the ball itself left out, go through a probabilistic Hough transform. The shaft is the longest segment at least 5 ball radii long, at least 35 degrees from level, whose line crosses the ball's center row within 4 ball diameters of the ball. Where it crosses is the hosel.
3. **Strike** is the ball's center minus the hosel along that row, in ball diameters, larger toward the toe. The diameter is the session's median over the swings with a contact frame: one ball is 12 to 22px across, and its own size read a pixel off moved a strike by up to 15% between runs. It is blank when the shaft is not found in the contact frame: an earlier frame has the club short of the ball and reads a toe strike that was not there.

The impact zoom is centered on the contact frame and its rows are labeled from contact. `swing-NN/strike.json` has the strike, the contact time, the hosel point and the ball. The camera sees the face at a slant and its distance changes the scale, so Strike ranks swings within a session and is not inches on the face.

On the 2026-11-14 sim session (IMG_4877, 5 swings) the contact frame is the one the owner picked by eye on all 5, and the order from the hosel out (01, 05, 02, 03, 04, from 1.44 to 2.17 diameters) is the owner's. Its rank correlation with smash is -0.90 and with side spin -0.80 (a toe strike draws). On the 2026-11-08 session (IMG_4859) 11 of 15 swings read, and the correlations are -0.58 and -0.43. Its 4 blanks are 2 good, 1 poor and 1 ungraded swing. The 2026-10-31 sessions are filmed in dimmer light, and the shaft at contact is a wide blur there: IMG_4761 reads 1 of 7 swings and IMG_4762 2 of 9.

### Sim readout
The simulator draws its numbers in a 2x6 panel at the top right of the screen. The fields, in panel order: club_path, carry_game, face_to_path, carry_raw, back_spin, total_length, smash, ball_speed, peak_height, total_spin, face_to_target, club_speed. The pipeline reads them locally with glyph templates: no model and no export from the simulator. The simulator has shown two layouts: uppercase labels, and mixed-case labels in a panel about 15% wider for its height. The left column's value cell keeps only 1px of margin on its left side (the rest keep 3px), because the box can start a few px inside the panel's edge.
- **Finding the panel**: a Canny edge reference (`sim_panel.png`, the uppercase layout) is matched at several scales, then its width is stretched up to 20% and its height fit again, so the wider layout gets a box out to both edges. Per video, one frame at each of 3, 4, and 5s after the first 5 confirmed candidates is searched, before the detection dedupe so the screen-change rule can use the box, and only when that offset falls inside the candidate's shot window. The readout then reads through the same box. The box wins when at least 3 panels agree within 2px on every edge and they are more than half of the panels found (not of all probed frames), and the box must read at least 4 fields in one of those frames, so a panel-shaped box with no numbers is not the readout. Otherwise there is no Sim output for the video. Each swing then searches the second and fourth frames it reads (impact + 2.5s and 3.5s) again, and when a panel is found there but none of the found boxes is within 5px of the session's on every edge, the camera moved and that swing's fields are left blank. A frame where no panel is found (someone standing in front of the screen) does not count, and when neither frame finds one the swing reads through the session's box. Reads stay right with the box off by up to 5px every way, and a camera bump that moves the panel a row moves it about 40px.
- **Reading a cell**: the color distance from the cell's median color is thresholded, the glyphs are cut out, each is matched to its nearest neighbor in `sim_glyphs.npz`, and the result must fit the field's fixed format. A read in doubt leaves the cell blank, because a wrong number is worse than a blank: a glyph that matches two classes closely and is not clearly the first where the two differ (an 8 shares most of its ink with a 6, 0 or 9, so a close pair is matched again with each pixel weighted by how far apart the two classes' mean templates are there), a lost or broken digit, a whole number that starts with 0, a spin of fewer than 3 digits (no shot spins under 100 rpm), a digit cut by the cell's left or right edge, or a fifth of the digit band off the background color and away from every glyph (a patch over part of the cell). A patch close to the panel's own color still hides digits without a trace: on synthetic hard-edged patches over the test panels, 934 of 39,360 reads (2.4%) were a shorter, well-formed wrong number, and none on real footage.
- **Voting**: each swing's frames are sampled twice a second from impact + 2s to the earliest of impact + 15s, the next swing's clip start, and the video end. A value needs at least 2 votes and twice the runner-up's. Carry (game) and total length count up while the ball flies and rolls, and the roll can hold a value short of the final one for longer than the final reads, so they vote only on frames from impact + 13s on and are left blank when the window ends before then.
- **Unchanged screen**: the screen switches to a new shot 0.2 to 0.5s after impact, so the frames from 1.5s to 0.25s before impact (sampled the same way, and never earlier than 1s after the previous swing's impact) still show the previous shot. When at least 4 fields read both there and in the voted window, and more than half of them are equal, the simulator registered no shot (a false detection or a missed hit): every field is left blank. The same check runs against the latest swing that has numbers, which covers a panel hidden behind the golfer before impact: two real shots do not share most of their numbers.
- **Output**: `swing-NN/sim.json` is `{"panel": [x0, x1, y0, y1], "frames": n, "unchanged": bool, "fields": {name: {"text", "value", "votes", "seen_s"}}}`, and `session.md` gets the `## Sim` table. A row of blanks can mean the next swing started before the numbers showed, or that the screen did not change.
- **Layout**: the simulator's tiles can be moved, and the titles under the numbers are about 7px tall in a 1080p video, too small to tell apart (matching each title's picture mislabels several tiles on every fixture). The numbers say what they are instead. Only a club path ends in I-O or O-I, only a face ends in C or O, only smash has two decimals, only spin has none, and the rest are tied together by what the simulator computes: smash is ball speed over club speed (within 0.011), face to target is club path plus face to path (within 0.16 degrees), total spin is at least back spin, carry (game) is near carry (raw), and total length is at least carry (game). On 94 published and labeled shots the worst misses are 0.006 and 0.1. Frames from 13.5, 14.5, 16 and 19s after each of the first 5 impacts (those before the next swing) are each read as every kind of number, late enough that carry (game) and total length have stopped counting up, since a count still running breaks the checks for the true layout and can fit a wrong one. On those frames a cell's kind is the one it reads as most, and every way of handing the fields of a kind to its cells is tried against those checks. The layout that holds in the most frames is used when it holds in 2 and no other does as well, with ties going to the layout nearest the one the simulator ships. Carry (game) and carry (raw) show nearly the same number, so they keep their shipped order. When the numbers do not settle a layout (a cell that never reads, one shot whose numbers fit two layouts) the panel is read as shipped, unless the shipped layout breaks those checks in more frames than it holds in: then no tile is read. A tile moved together with a partner that never reads all session is the case this cannot see.
- **Consistency**: each swing's voted numbers go through the same checks, and the fields of a check that fails are blanked for that swing. That catches a misread that is well formed, such as a back spin above total spin.
- **Left panel**: the simulator shows a second panel on the left of the screen with side spin, the launch angle (VLA), the side launch angle (HLA) and offline, next to six numbers the readout panel also shows (club speed, back spin, club path, face to target, face to path, peak height). Those six find it and vouch for it. The search takes one frame 1s before and 5s and 9s after each of the first 6 impacts in which the readout panel reads 3 of the six, matches each one's value cell from the readout panel against the frame left of it at 0.8 to 1.15 of its size, and keeps the 2 by 6 grid the most matches sit on. A grid counts when 3 of the six read the same through it as on the readout panel, 2 frames must give the same box within 4px, and each edge of the box then settles up to 6px to where the most cells read on the 8 of those frames that read the most of the panel. Each swing's left panel is read in the frames of its shot window and in 12 more spread evenly from the end of that window to 0.75s before the next impact (20s after the window for the last swing), because the golfer stands in front of it on and off and the screen shows the shot until the next one. A frame's left panel counts only when 2 of the six read in it and every one that reads equals the shot's readout value, so a covered panel, a moved one and one still showing the previous shot are all left out. The four fields vote like the readout's. A leading minus sign is read on side spin, HLA and offline only. Side spin is blanked unless its square is total spin squared less back spin squared, within the rounding of the three, when both were read. On IMG_4877 side spin, VLA and HLA read on all 5 swings and offline on the 2 with two digits before the point; a one-digit offline over the scenery at the bottom of the panel mostly does not read. `sim.json` has the box as `left_panel`, and the Sim table has the four as its last columns.
- **Rebuilding the glyphs**: hand-label shots in `local/sim_labels.yaml` as `videos: [{path, shots: [{impact, <field>: "text"}]}]`, quoting every value. `carry_game` and `total_length` are not labeled because they count up while the ball flies. A video entry with `glyphs: false` is scored but kept out of the glyph build. Use it for a video whose screen draws a different font: `build_glyphs` keeps 80 templates per class spread over its input, so a second font's glyphs push out the first font's. The 2026-11-08 session is one, and `sim-glyphs` names the videos it left out. Run `golf-etl sim-glyphs local/sim_labels.yaml src/golf_etl/sim_glyphs.npz`, then `golf-etl eval-sim local/sim_labels.yaml`, which holds each video out in turn. Labeled eval windows start at impact + 3s, later than the pipeline's 2s, to leave margin past the switch to this shot's numbers 0.2 to 0.5s after impact. The current held-out result is 358 of 360 labeled fields right, 0 wrong, 2 blank, over 36 shots in 5 videos (two normal, one 240fps slo-mo, and the 2026-11-08 and 2026-11-14 videos with their `glyphs: false` entries, read with the other three's templates). The committed glyphs read the same 360 fields at 359 right, 0 wrong, 1 blank.

### Retention
- The original is deleted from the inbox once its session is pushed.
- A sweep at the end of every run deletes `failed/` originals older than 3 days (`GOLF_FAILED_TTL_DAYS`), aged from their `.error.txt` since a video's own time is when it was shot.
- Sessions in `golf-sessions` are never deleted: there is no TTL or size cap. Re-sending a video replaces its session in a new commit.

### Publishing to GitHub
- Each publish starts from an empty work directory under `GOLF_SCRATCH_DIR`: a depth-1, blob-filtered fetch of `main` with only that session checked out, plus `index.md`, `chain.md` and every session's `summary.json` and marker, which they are built from. A run never downloads the repo's history however many sessions it holds.
- The session folder is replaced, the marker written, `sessions/index.md` and `sessions/chain.md` rebuilt, and one commit `publish <session-id>` is pushed to `main` as `golf-etl <golf-etl@localhost>`. `clip.mp4` is left out.
- The same code can verify a push: `published_files` lists a session's paths and sizes as the remote has them.

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

## Repo
- Python 3.12, `requirements.txt` with exact pins, Dockerfile on `python:3.12-slim` with ffmpeg, git, openssh-client, and the bundled Pose Landmarker lite model.
- CLI:
  - `golf-etl process <video> --out <dir>`: local run, no inbox or GitHub
  - `golf-etl eval <labels.yaml>`: detection precision, recall, and timing error against hand-labeled impact timestamps
  - `golf-etl poll`: the scheduled entrypoint
  - `golf-etl upload-server`: the upload page, its status, and tusd's hooks
- Tests:
  - onset detection against synthetic audio (clicks plus noise)
  - frame selection against canned pose sequences
  - every row of the idempotency table and every failure scenario against a fake store (`tests/fake_store.py`); the inbox layer sits behind the small `Store` interface for this
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
- **Practice swing next to a shot**: on a sim video, a swing the sim registered is kept even when a faster-handed practice swing lands within 8s of it, and the practice swing is rejected.
- **Phone distance**: hand speed is measured in leg lengths, so the same swing confirms the same way filmed close or far.
- **Single-swing clip**: a video under 15 seconds emits at most one swing.
- **No swing found**: the session is written with `session.md` stating zero swings, and it is not a failure.

### Swing frames for Claude
Each swing gets labeled JPEGs decoded from the original video, 2560px long edge or smaller and never upscaled.
- **Detected swing**: the folder contains `pose.csv`, `ball.json`, `05-impact-zoom.jpg`, `06-sequence.jpg`, and `07-after-shot.jpg`, each JPEG sRGB and labeled with position and time in the video. It has no `clip.mp4`.
- **Sim readout**: when the panel is found, each swing also has `sim.json` and `session.md` has a `## Sim` table. Cells that cannot be read confidently are blank, never guessed.
- **Moved tiles**: a readout whose tiles are not where the simulator ships them reads each number under its own name, and numbers that contradict each other are blank.
- **Left panel**: side spin, launch, side launch and offline are in the Sim table when the left panel is found, read only from frames whose left panel shows the same shared numbers as the readout, and blank otherwise.
- **Sim grade**: on a sim session with at least 3 smash reads, Grade comes from the sim's smash factor and a swing without a smash read has no grade.
- **Impact zoom**: `05-impact-zoom.jpg` shows a native-resolution band at ball height for the frames before, at, and after impact, with the ball in it from either camera angle.
- **iPhone footage**: HDR, portrait, APAC-plus-AAC `.mov` files process like any other video, with natural color.
- **Blurry target frame**: for address, top, and finish, a sharper frame within two frames is used instead.
- **Long videos**: times of a minute or more read as minutes and seconds.

### Ball flight grade
Each swing's ball flight is graded against the session in `session.md`, so a review can rank swings from text alone.
- **Tracked swing**: the swing's row has ball px/frame, start direction, `ok`, and a grade (on the smash basis, only when its smash was read), and `ball.json` has the chain points.
- **Yellow dot test**: a synthetic clip with a ball launched at a known speed returns that speed within 5%.
- **Untracked swing**: the row has a `lost:` reason and empty metric cells, never a guessed grade; on the smash basis it keeps the grade its smash read gives.
- **Size**: `session.md` stays under 50KB so it reads in one call.

### Swing measures
`session.md` carries what a review would otherwise estimate from the pictures.
- **Sequence sheet**: every panel of a swing is the same crop around the golfer, the club and the ball, never upscaled, with no pose drawn on it.
- **Takeaway start**: it is the club leaving the ball within two frames, and a swing where the club is not seen leaving is marked `hands`.
- **Body table**: one row per swing with the view and each measure in leg lengths or degrees, blank where the pose did not see the joint.
- **By grade table**: written when the swings have more than one grade, with the mean of each number per grade and the swing count of each grade.
- **Index**: after a publish `sessions/index.md` has a row for every session in the repo, and publishing the same session again changes nothing.

### Retention
- **Original after success**: deleted from the inbox.
- **Failed originals**: older than 3 days they are deleted, honoring the override.
- **Sessions on GitHub**: never swept.

### Failure handling
- **Processing error**: on the first or second failure the video returns to `inbox/` with its attempt count incremented.
- **Third failure**: the video moves to `failed/` with `<name>.error.txt`.
- **Run killed mid-video**: the next run moves it back to `inbox/` and counts the attempt.
- **Re-upload of a failed video**: the failed copy and its error file are deleted and the new upload starts with a fresh attempt count.
- **Status**: `state.json` has each recent upload's latest status; nothing about failures is pushed to `golf-sessions`.

## Coaching skill
The owner's claude.ai coaching skill lives in the ai-loadout repo (github.com/rumstead/ai-loadout, `skills/golf-swing-analysis/`), and each `golf-swing-analysis` release there attaches `golf-swing-analysis.zip` for uploading to claude.ai. It triggers on requests like "review my latest golf session", finds the newest session folder in the `golf-sessions` repo (names sort by capture time), and reads `session.md`, `sessions/index.md` for how the numbers have moved across sessions, and `golfer.md` at the repo root when there is one. `golfer.md` is the golfer's own file (handicap, usual miss, what they are working on); the pipeline never writes it. The skill skips swings with no ball launched, picks up to 5 swings by ball flight grade (or spread across the session without grades) unless told otherwise, views each swing's sequence sheet and impact zoom, takes simulator numbers from the Sim table and the cells it leaves blank from `07-after-shot` (never from earlier frames, which show the previous shot), uses the Timing, Body and By grade tables for measurements, asks about ball flight shape once per session when there are no screen numbers, and names the source video and the Photos scrub time for every swing it mentions, then runs the coaching steps. It is uploaded to claude.ai by hand; claude.ai's copy is the one that runs.

## Risks / Trade-offs
- `latest` with `Always` means a bad push breaks the next run → failures are retried and land in `failed/`, and the original is kept for 3 days.
- Audio onsets at a busy range → pose confirmation plus the eval harness. Rejected candidates in `session.md` make misses visible.
- The sessions repo only grows → a session is about 0.93 to 2.4MB per swing, and GitHub recommends repos under 10GB.
- The deploy key can write to `golf-sessions` → it has no other access, and it does not expire.

## Follow-ups
- The upload page that fills the inbox
- Strike photos: impact tape or foot spray photos in the inbox, attached to the session recorded just before them
- Notification when a session is ready
- Claude API analysis and an MCP server for sessions
