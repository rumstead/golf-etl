# golf-etl
Break down golf swing videos into images for AI analysis.

Put a swing video in `inbox/` under `GOLF_INBOX_ROOT` (`/data` by default). A few minutes later `sessions/<session>/` in the private GitHub repo `rumstead/golf-sessions` has a folder per swing with an eight-position sequence sheet cropped to the golfer, an impact zoom, a frame a few seconds after impact that catches simulator or launch monitor numbers, and the pose estimate as a CSV of joint positions. `session.md` grades each swing's ball flight (how fast the ball leaves the club, against the rest of the session), gives each swing's time in the video, times the backswing and downswing, measures how the head, hips and hands moved, and averages every number over the good and the poor swings, so good and bad swings can be picked out and compared from text alone. `sessions/index.md` has one row of medians per session. Images are full size (2560px long edge) so launch monitor numbers stay legible. Add `golf-sessions` in claude.ai with **+ > Add from GitHub** and ask it to review the session for coaching.

How it works is in [docs/design.md](docs/design.md). It runs as a CronJob in [rumstead/homelab](https://github.com/rumstead/homelab) (`kubernetes/manifests/golf-etl`).

From the phone, videos go up through the upload page that `golf-etl upload-server` serves behind nginx and tusd in the same cluster. Uploads resume after a dropped connection, and only finished ones land in `inbox/`.

## Settings
Every setting in `src/golf_etl/config.py` can be overridden with `GOLF_<NAME>`.

| Variable | Default | |
|---|---|---|
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
The claude.ai skill that knows how to read a session lives in the [ai-loadout](https://github.com/rumstead/ai-loadout) repo at `skills/golf-swing-analysis/`. It finds the newest folder in `sessions/` of the `golf-sessions` repo (add the repo to the chat with **+ > Add from GitHub** first), reads `session.md`, `sessions/index.md` and your `golfer.md` (handicap, usual miss, what you are working on; you keep it at the root of `golf-sessions`), looks at each swing's full-size sequence sheet and impact zoom, uses the ball flight, timing and body numbers and the simulator numbers it reads off `07-after-shot.jpg`, asks about ball flight once when they do not settle it, and coaches. It names the source video and the time to scrub to in Photos for every swing it mentions. Upload the `golf-swing-analysis.zip` from the latest `golf-swing-analysis` release in that repo in claude.ai's skill settings (replacing an older copy), then ask:

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
    no_ball: [9.1]  # optional: practice swings and other motions that hit no ball, not scored
```

```sh
podman run --rm -v "$PWD":/repo:Z -w /repo golf-etl:test python -m pytest -m real
podman run --rm --user root -v "$PWD/local":/w:Z golf-etl eval /w/labels.yaml
```

Thresholds are environment variables (`GOLF_ONSET_DELTA`, `GOLF_IMPACT_AUDIO_LAG_MS`, ...); see `src/golf_etl/config.py`.
