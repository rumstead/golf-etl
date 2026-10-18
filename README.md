# golf-etl
Break down golf swing videos into images for AI analysis.

Put a swing video in `inbox/` under `GOLF_INBOX_ROOT` (`/data` by default). A few minutes later `sessions/<session>/` in the private GitHub repo `rumstead/golf-sessions` has a folder per swing with an eight-position sequence sheet, an impact zoom, a frame a few seconds after impact that catches simulator or launch monitor numbers, and the pose estimate as a CSV of joint positions. `session.md` grades each swing's ball flight (how fast the ball leaves the club, against the rest of the session) and gives each swing's time in the video, so good and bad swings can be picked out from text alone. Images are full size (2560px long edge) so launch monitor numbers stay legible. Add `golf-sessions` in claude.ai with **+ > Add from GitHub** and ask it to review the session for coaching.

How it works is in [docs/design.md](docs/design.md). It runs as a CronJob in [rumstead/homelab](https://github.com/rumstead/homelab) (`kubernetes/manifests/golf-etl`).

From the phone, videos go up through the upload page that `golf-etl upload-server` serves behind nginx and tusd in the same cluster. Uploads resume after a dropped connection, and only finished ones land in `inbox/`.

## Settings
Every setting in `src/golf_etl/config.py` can be overridden with `GOLF_<NAME>`.

| Variable | Default | |
|---|---|---|
| `GOLF_STORAGE` | `github` | `github` or `drive` |
| `GOLF_INBOX_ROOT` | `/data` | the root folder, holding `inbox/`, `processing/`, `failed/` and `.golf-etl/state.json` |
| `GOLF_SESSIONS_REPO` | `git@github.com:rumstead/golf-sessions.git` | where sessions are pushed |
| `GOLF_SSH_KEY_PATH` | `/etc/golf-etl/ssh/id_ed25519` | deploy key with write access to the repo |
| `GOLF_SCRATCH_DIR` | `/scratch` | working space for videos and the git checkout |
| `GOLF_FAILED_TTL_DAYS` | `3` | how long failed originals are kept |

Sessions on GitHub are never deleted. Poll once with `golf-etl poll`; it exits with status 2 and a message when the deploy key is missing.

## Recording
- Phone on a tripod, whole body and the ball in frame, face-on or down the line.
- Use slo-mo (120fps or more) in daylight. The faster shutter cuts blur, and at 120fps the impact zoom usually catches the club on the ball. Ball flight grading needs 100fps or more.
- Keep the ball where the camera sees it at rest and for a few feet after it leaves; the grade follows it up the frame. Face-on the ball crosses the frame in two or three frames, so ball grades come from down the line.
- Sound on. Swings are found by the impact sound.

## Coaching in claude.ai
`claude/skills/golf-swing-analysis/` is the claude.ai skill that knows how to read a session: it finds the newest folder in `sessions/` of the `golf-sessions` repo (add the repo to the chat with **+ > Add from GitHub** first), reads `session.md`, looks at each swing's full-size sequence sheet and impact zoom, uses the ball flight, timing, and simulator numbers it finds, asks about ball flight once when they do not settle it, and coaches. It names the source video and the time to scrub to in Photos for every swing it mentions. Zip the folder and upload it in claude.ai's skill settings (replacing an older copy), then ask:

```
review my latest golf session
```

## Run it locally
Everything runs in the container, which has ffmpeg and the pose model.

```sh
podman build -t golf-etl .
podman run --rm --user root -v "$PWD":/work:Z golf-etl process /work/swing.mov --out /work/out
```

## Tests
```sh
podman build --target test -t golf-etl:test .
```
That is what CI runs: ruff and the unit tests.

Real footage lives in `local/` (gitignored) next to a `local/labels.yaml`:

```yaml
tolerance_ms: 17  # two frames at 120fps
videos:
  - path: IMG_4439-a.mov
    impacts: [4.368]  # first frame with the clubface on the ball
```

```sh
podman run --rm -v "$PWD":/repo:Z -w /repo golf-etl:test python -m pytest -m real
podman run --rm --user root -v "$PWD/local":/w:Z golf-etl eval /w/labels.yaml
```

Thresholds are environment variables (`GOLF_ONSET_DELTA`, `GOLF_IMPACT_AUDIO_LAG_MS`, ...); see `src/golf_etl/config.py`.

## Drive mode
`GOLF_STORAGE=drive` keeps the Drive inbox `golf/inbox` and Drive sessions in `golf/sessions/`. Each swing also has a short `clip.mp4`, and the sequence sheet, impact zoom, and after-shot frame have copies under 72KB that the claude.ai Drive connector can open. Sessions expire after 60 days, clips after 14, and the oldest sessions go when they total over 3GiB (`GOLF_SESSION_TTL_DAYS`, `GOLF_CLIP_TTL_DAYS`, `GOLF_MAX_BYTES`). The pipeline reaches Drive through rclone.

1. On any machine with rclone and a browser, run `rclone authorize "drive"`, approve, and copy the JSON token it prints.
2. Put these in a `drive.env` (homelab keeps them in a SOPS secret). `--env-file` takes each line as is, so no quotes around the token:
   ```
   RCLONE_CONFIG_GDRIVE_TYPE=drive
   RCLONE_CONFIG_GDRIVE_SCOPE=drive
   RCLONE_CONFIG_GDRIVE_TOKEN={"access_token":...,"refresh_token":...}
   RCLONE_DRIVE_USE_TRASH=false
   ```
3. Run one poll by hand:
   ```sh
   podman run --rm --env-file drive.env -e GOLF_STORAGE=drive -e GOLF_SCRATCH_DIR=/tmp/scratch golf-etl poll
   ```

To move the sessions in Drive to GitHub, run `golf-etl migrate-drive --dry-run`, then without the flag. It needs both the Drive environment above and the deploy key, refuses while anything is in the Drive `inbox/`, `processing/` or `failed/`, and deletes a session from Drive only after the push has every file at the same size.
