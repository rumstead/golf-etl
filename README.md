# golf-etl
Break down golf swing videos into images for AI analysis.

Share a swing video to the Google Drive folder `golf/inbox`. A few minutes later `golf/sessions/<session>/` has a folder per swing with an eight-position sequence sheet, an impact zoom, full-resolution key frames (address, top, impact, finish), a frame a few seconds after impact that catches simulator or launch monitor numbers, and a short clip. Ask claude.ai to review the session for coaching.

How it works is in [docs/design.md](docs/design.md). It runs as a CronJob in [rumstead/homelab](https://github.com/rumstead/homelab) (`kubernetes/manifests/golf-etl`).

## Recording
- Phone on a tripod, whole body and the ball in frame, face-on or down the line.
- Use slo-mo (120fps or more) in daylight. The faster shutter cuts blur, and at 120fps the impact zoom usually catches the club on the ball.
- Sound on. Swings are found by the impact sound.

## Coaching in claude.ai
`claude/skills/golf-swing-analysis/` is the claude.ai skill that knows how to read a session: it finds the newest folder in `golf/sessions`, reads `session.md`, looks at each swing's sequence sheet and impact zoom, asks about ball flight once, and coaches. Zip the folder and upload it in claude.ai's skill settings (replacing an older copy), then ask:

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

## Drive credentials
The pipeline reaches Drive through rclone, which has its own verified Google app, so there is no Google Cloud setup.

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
   podman run --rm --env-file drive.env -e GOLF_SCRATCH_DIR=/tmp/scratch golf-etl poll-drive
   ```
