# golf-etl GitHub Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** golf-etl reads videos from a local folder and pushes review files to the private `rumstead/golf-sessions` repo when `GOLF_STORAGE=github` (the default), keeps today's Drive behavior under `GOLF_STORAGE=drive`, can migrate the Drive sessions to GitHub, and the skill reads sessions from GitHub.

**Architecture:** The `drive` package becomes `storage`. Two seams: the inbox is a `Store` (the existing path-addressed protocol; `RcloneStore` already works on a `gdrive:` remote or a local path), and sessions go to a `Publisher` (`DrivePublisher`, today's atomic swap plus TTL sweep, or `GitHubPublisher`, a sparse shallow git checkout that commits and pushes one session folder). `Poller` only talks to those two. The CLI picks both from `GOLF_STORAGE`.

**Tech Stack:** Python 3.12, pytest, ruff, rclone, git and OpenSSH in the image, ffmpeg and MediaPipe (unchanged).

**Out of this plan:** running the Drive migration and switching the cluster to github mode. Both happen in plan 3's cutover, after the upload page exists, so new Drive uploads keep working until then.

**Spec:** `docs/superpowers/specs/2026-10-17-github-sessions-design.md`

## Global Constraints

- `GOLF_STORAGE` is `github` (default) or `drive`; `drive` is exactly today's behavior. No mixed modes.
- Published to GitHub per session: `session.md`, `.golf-etl.json`, and per swing `06-sequence.jpg`, `05-impact-zoom.jpg`, `07-after-shot.jpg`, `ball.json`, `pose.csv`. Never `clip.mp4`, `*-small.jpg`, or the original video.
- Sessions live at `sessions/<session-id>/` in `golf-sessions`, same ids as today. Never deleted from GitHub. Reprocessing replaces the folder in a new commit.
- A publish must not download the whole repo history: shallow, blob-filtered, sparse.
- Credential is a deploy key with write access to `golf-sessions` only. Host keys are pinned, never trusted on first use.
- Image budget: a 30 swing session about 30MB or less on GitHub, with skeleton keypoints, ball, shaft, and every launch monitor number on `07-after-shot.jpg` legible.
- Times of a minute or more read `12:07.4` (minutes, seconds to one decimal) in `session.md` and image labels; under a minute stay `4.37s`.
- Existing behaviors (claiming, recovery, retry counts, `failed/` after 3 attempts, duplicates, re-upload of a failed video, atomic replace) keep working in both modes.
- The deployed cronjob runs `poll-drive` with no `GOLF_STORAGE` set. It must stay in Drive mode until plan 3's cutover.
- Commits: `<type>: <subject>` or `<type>(<scope>): <subject>`, subject only, `git commit -s`, no AI attribution. PR bodies and code comments follow the `writing-as-rumstead` skill. No em or en dashes anywhere. Docs and comments describe what is true now.
- CI is the Dockerfile `test` target: `ruff check src tests && ruff format --check src tests && python -m pytest -q`. Run it as `podman build --target test -t golf-etl:test .` before each commit, or the same three commands in a container with the repo mounted at `/code`.

## Review Focus

1. A swing time that rounds up across a minute, `119.96` must read `2:00.0`, never `1:60.0`. Task 3 adds `test_video_time_rounds_into_the_next_minute`.
2. The first publish to an empty `golf-sessions` (no `main` branch yet) must create `main`, not fail. Task 6 adds `test_first_publish_to_an_empty_repo_creates_main`.
3. Republishing an unchanged session must not fail on an empty commit. Task 6 adds `test_republishing_the_same_files_makes_no_commit`.
4. The deployed cronjob flipping to GitHub mode when this merges: Task 1 pins `GOLF_STORAGE=drive` in homelab first, and Task 12 does not merge until that is live.
5. A migration that deletes a Drive session whose files did not all arrive: Task 8 adds `test_migration_keeps_a_drive_session_whose_push_is_missing_a_file`.

---

### Task 1: Pin the cluster to Drive mode (homelab)

**Files:**
- Modify: `/home/rumstead/homelab/kubernetes/manifests/golf-etl/configmap.yaml`

- [ ] **Step 1: Branch and edit**

```bash
cd /home/rumstead/homelab && git checkout main && git pull && git checkout -b fix/golf-etl-pin-drive
```
Add `GOLF_STORAGE: drive` to the ConfigMap `data`, above `GOLF_REMOTE`.

- [ ] **Step 2: Check it renders**

```bash
kubectl apply --dry-run=client -f kubernetes/manifests/golf-etl/configmap.yaml
```
Expected: `configmap/golf-etl-config configured (dry run)` or `created (dry run)`.

- [ ] **Step 3: Commit, push, PR, ask the owner to merge**

```bash
git add kubernetes/manifests/golf-etl/configmap.yaml
git commit -s -m "fix(golf-etl): pin the pipeline to drive storage"
git push -u origin fix/golf-etl-pin-drive
```
PR body: one or two lowercase sentences saying golf-etl is getting a github storage mode as its default, and this keeps the cluster on drive until the cutover. After merge, confirm the live ConfigMap has it: `kubectl -n golf-etl get cm golf-etl-config -o jsonpath='{.data.GOLF_STORAGE}'` → `drive`.

### Task 2: Rename the `drive` package to `storage`

Pure rename, no behavior change.

**Files:**
- Move: `src/golf_etl/drive/{client,poller,rclone,retention}.py` → `src/golf_etl/storage/{store,poller,rclone,retention}.py` (`git mv`), `src/golf_etl/drive/__init__.py` → `src/golf_etl/storage/__init__.py`
- Move: `tests/fake_drive.py` → `tests/fake_store.py`, `tests/test_rclone_drive.py` → `tests/test_rclone_store.py`
- Modify: every import of `golf_etl.drive` (`cli.py`, the moved modules, `tests/test_poller.py`, `tests/test_retention.py`)

**Interfaces:**
- Produces: `golf_etl.storage.store` with `Entry`, `Store` (was `Drive`), `ensure_layout`, `State`, and the constants `INBOX`, `PROCESSING`, `FAILED`, `SESSIONS`, `STATE`, `MARKER`, `VIDEO_SUFFIXES`; `golf_etl.storage.rclone.RcloneStore` (was `RcloneDrive`, same constructor `(root: str, rclone: str = "rclone")`) and `RcloneError`; `tests.fake_store.FakeStore` (was `FakeDrive`). Parameters and locals named `drive` that hold a store become `store`.

- [ ] **Step 1: `git mv` the files, rename the classes and imports**

Module docstring of `store.py`: `"""A folder tree addressed by path: the inbox, and in drive mode the sessions too.\n\nRcloneStore implements it on a Drive remote or a local folder; tests use an in-memory fake.\n"""`.

- [ ] **Step 2: Run CI**

Run: `podman build --target test -t golf-etl:test .`
Expected: build succeeds (ruff clean, all tests pass).

- [ ] **Step 3: Commit**

```bash
git checkout -b feat/github-sessions
git add -A src tests
git commit -s -m "refactor: rename the drive package to storage"
```

### Task 3: Minutes and seconds for long videos

**Files:**
- Modify: `src/golf_etl/session.py` (new `video_time`, used in the swing table, Timing table times, rejected table)
- Modify: `src/golf_etl/pipeline.py` (sequence labels and the after-shot label use `video_time`)
- Test: `tests/test_session.py`

**Interfaces:**
- Produces: `golf_etl.session.video_time(t: float) -> str`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(
    "t, text",
    [(4.369, "4.37s"), (59.99, "59.99s"), (60.0, "1:00.0"), (727.4, "12:07.4"), (3725.04, "62:05.0")],
)
def test_video_time_switches_to_minutes_at_a_minute(t, text):
    assert video_time(t) == text


def test_video_time_rounds_into_the_next_minute():
    assert video_time(119.96) == "2:00.0"


def test_session_md_shows_long_video_times_in_minutes(tmp_path):
    # build a SessionSummary like test_session_md_has_a_timing_table, with one swing at
    # impact_s=727.4 and one rejected candidate at 61.2s
    text = (tmp_path / "session.md").read_text()
    assert "| swing-01 | 12:07.4 |" in text
    assert "| 1:01.2 |" in text
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/test_session.py -k "video_time or long_video" -q`
Expected: FAIL with `ImportError: cannot import name 'video_time'`.

- [ ] **Step 3: Implement `video_time` and use it**

Under a minute: `f"{t:.2f}s"`. Otherwise round to tenths first (`tenths = round(t * 10)`, `m, rem = divmod(tenths, 600)`) then `f"{m}:{rem / 10:04.1f}"`. Use it for `swing.impact_s`, the three `(... s)` times in the Timing row, `r.time_s`, the sequence panel labels (`f"{name} {video_time(ft)}"`), and `f"after shot {video_time(ft)}"`. Durations (backswing, downswing) and error messages keep `.2f`s.

- [ ] **Step 4: Run CI**

Run: `podman build --target test -t golf-etl:test .`
Expected: passes. Existing assertions on `4.37s`-style text still hold since their times are under a minute.

- [ ] **Step 5: Commit**

```bash
git add src/golf_etl/session.py src/golf_etl/pipeline.py tests/test_session.py
git commit -s -m "feat: show times past a minute as minutes and seconds"
```

### Task 4: The publisher seam and storage settings

**Files:**
- Create: `src/golf_etl/storage/publish.py`
- Modify: `src/golf_etl/storage/poller.py` (drop `publish` and `session_checksum`; take a publisher)
- Modify: `src/golf_etl/storage/retention.py` (split `sweep`)
- Modify: `src/golf_etl/config.py`
- Modify: `src/golf_etl/cli.py` (build `DrivePublisher`; same behavior)
- Test: `tests/test_poller.py`, `tests/test_retention.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Settings.storage: str = "github"`, `Settings.inbox_root: str = "/data"`, `Settings.sessions_repo: str = "git@github.com:rumstead/golf-sessions.git"`, `Settings.ssh_key_path: str = "/etc/golf-etl/ssh/id_ed25519"`, grouped under a `# storage` comment that says `remote` is the inbox and sessions in drive mode and `inbox_root` is the inbox in github mode.
  - `golf_etl.storage.publish.Publisher` protocol: `publish(self, session_dir: Path, session_id: str, marker: dict) -> None` and `sweep(self, now: datetime) -> SweepReport`.
  - `golf_etl.storage.publish.marker(checksum: str, source_name: str, version: str) -> dict` returning the same four keys the marker has today (`sha256`, `sourceName`, `pipelineVersion`, `published`).
  - `golf_etl.storage.publish.DrivePublisher(store: Store, cfg: Settings)`: `publish` is today's `Poller.publish` (upload to `.tmp-<id>`, write the marker, swap); `sweep` is today's session part of `sweep`.
  - `golf_etl.storage.retention.sweep_failed(store: Store, cfg: Settings, now: datetime) -> SweepReport` (the failed-originals part) and `sweep_sessions(store, cfg, now) -> SweepReport` (the rest). `sweep` is removed.
  - `Poller(inbox: Store, publisher: Publisher, cfg: Settings, process: Processor, scratch: Path, version: str)`.

- [ ] **Step 1: Update the tests first**

In `tests/test_poller.py` the fixture becomes `Poller(store, DrivePublisher(store, Settings()), Settings(), process, tmp_path / "scratch", "test")`. In `tests/test_retention.py`, failed-original tests call `sweep_failed`, session tests call `sweep_sessions`. Add to `tests/test_config.py`:

```python
def test_storage_defaults_to_github():
    s = Settings()
    assert (s.storage, s.inbox_root) == ("github", "/data")
    assert s.sessions_repo == "git@github.com:rumstead/golf-sessions.git"


def test_storage_is_overridable():
    assert Settings.from_env({"GOLF_STORAGE": "drive"}).storage == "drive"
```

And to `tests/test_poller.py`:

```python
def test_poller_publishes_through_the_publisher(tmp_path):
    class Recorder:
        def __init__(self):
            self.calls = []

        def publish(self, session_dir, session_id, marker):
            self.calls.append((session_id, marker["sha256"], sorted(p.name for p in session_dir.iterdir())))

        def sweep(self, now):
            raise AssertionError("the poller does not sweep")

    store, rec = FakeStore(), Recorder()
    store.add_video("a.mov", data=b"aaa")
    Poller(store, rec, Settings(), FakeProcess(), tmp_path / "s", "test").run()
    sha = hashlib.sha256(b"aaa").hexdigest()
    assert rec.calls == [(f"2026-10-04-1200-{sha[:8]}", sha, ["session.md", "swing-01"])]
    assert store.names_in("sessions") == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/test_poller.py tests/test_retention.py tests/test_config.py -q`
Expected: FAIL on imports (`DrivePublisher`, `sweep_failed`) and the missing `storage` field.

- [ ] **Step 3: Implement**

Move the code as the Interfaces say. `ensure_layout` stops creating `sessions/` (the `DrivePublisher` creates it on first publish via `upload_tree`). In `cli.cmd_poll`, drive mode builds `store = RcloneStore(cfg.remote)` and `DrivePublisher(store, cfg)`, then runs `sweep_failed(store, ...)` and `publisher.sweep(...)` after the poll, logging both.

- [ ] **Step 4: Run CI**

Run: `podman build --target test -t golf-etl:test .`
Expected: passes, including every existing poller and retention test.

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -s -m "refactor: publish sessions through a publisher"
```

### Task 5: Recent upload status in the state file

The upload page (plan 3) reads this to show each upload's status.

**Files:**
- Modify: `src/golf_etl/storage/store.py` (`State`)
- Modify: `src/golf_etl/storage/poller.py`
- Test: `tests/test_poller.py`

**Interfaces:**
- Produces: `State.recent: list[dict]`, saved in `.golf-etl/state.json` next to `attempts`, newest first, at most 50. Each item: `{"name": str, "sha256": str, "status": "processing" | "retrying" | "failed" | "published", "updated": <ISO 8601 UTC>, "session": <session id, published only>, "error": <last line of the exception, retrying and failed only>}`. One item per `sha256`: a new status replaces the old item and moves it to the front. `State.note(name: str, sha256: str, status: str, now: datetime, **extra) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_status_tracks_a_video_from_processing_to_published(env):
    store, process, run = env
    store.add_video("a.mov", data=b"aaa")
    run()
    [item] = State.load(store).recent
    assert (item["name"], item["status"]) == ("a.mov", "published")
    assert item["session"].endswith(hashlib.sha256(b"aaa").hexdigest()[:8])


def test_status_shows_retrying_then_failed_with_the_error(env):
    store, process, run = env
    store.add_video("bad.mov", data=b"bad")
    process.fail_on.add("bad.mov")
    run()
    assert State.load(store).recent[0]["status"] == "retrying"
    run(); run()
    item = State.load(store).recent[0]
    assert item["status"] == "failed" and "cannot decode bad.mov" in item["error"]


def test_status_keeps_the_newest_fifty(env):
    store, process, run = env
    for i in range(55):
        store.add_video(f"v{i}.mov", data=f"v{i}".encode())
    run()
    assert len(State.load(store).recent) == 50
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/test_poller.py -k status -q`
Expected: FAIL with `AttributeError: 'State' object has no attribute 'recent'`.

- [ ] **Step 3: Implement**

`State.load` reads `recent` (default `[]`), `save` writes both keys. `Poller.handle` notes `processing` and saves before downloading, `published` after the publish; `attempt_failed` notes `retrying` or `failed` with `error` set to the last non-empty line of the traceback text.

- [ ] **Step 4: Run CI**

Run: `podman build --target test -t golf-etl:test .`
Expected: passes.

- [ ] **Step 5: Commit**

```bash
git add src/golf_etl/storage tests/test_poller.py
git commit -s -m "feat: keep each upload's latest status in the state file"
```

### Task 6: `GitHubPublisher`

**Files:**
- Create: `src/golf_etl/storage/github.py`
- Create: `src/golf_etl/storage/github_known_hosts`
- Modify: `pyproject.toml` (ship `github_known_hosts` as package data)
- Modify: `Dockerfile` (`git` and `openssh-client` in the apt line)
- Test: `tests/test_github_publisher.py`

**Interfaces:**
- Consumes: `Publisher`, `SweepReport` from Task 4.
- Produces:
  - `GitHubPublisher(remote: str, work: Path, ssh_key: str | None = None, branch: str = "main")`.
  - `publish(session_dir, session_id, marker)`: commits `sessions/<session_id>/` (the session's files minus `EXCLUDE`, plus `.golf-etl.json` from `marker`) and pushes. Commit message `publish <session_id>`, author and committer `golf-etl <golf-etl@localhost>`.
  - `published_files(self, session_id: str) -> dict[str, int]`: path under the session folder to size, read from the remote `main` after a fresh fetch. Task 8 uses it to verify.
  - `sweep(now) -> SweepReport()`: GitHub sessions never expire.
  - `EXCLUDE = ("clip.mp4", "*-small.jpg")` (fnmatch on file names).

- [ ] **Step 1: Write the failing tests**

A fixture makes a bare repo with `git init --bare -b main remote.git` and `git -C remote.git config uploadpack.allowFilter true`, and a helper `session(tmp_path, sid)` that writes `session.md`, `swing-01/06-sequence.jpg`, `swing-01/06-sequence-small.jpg`, `swing-01/clip.mp4`, `swing-01/pose.csv`. Tests use `remote=f"file://{remote}"` and read results with `git --git-dir remote.git ls-tree -r -l main`.

```python
def test_first_publish_to_an_empty_repo_creates_main(...):
    pub.publish(session(tmp_path, "s1"), "s1", marker("abc", "IMG_1.MOV", "test"))
    assert tree(remote) == {
        "sessions/s1/.golf-etl.json", "sessions/s1/session.md",
        "sessions/s1/swing-01/06-sequence.jpg", "sessions/s1/swing-01/pose.csv",
    }


def test_clips_and_small_copies_are_not_published(...):
    # covered by the tree above; assert neither name appears for a second session too


def test_second_session_keeps_the_first(...):
    # publish s1 then s2; both folders in the tree, two commits on main


def test_republishing_a_session_replaces_its_folder(...):
    # publish s1, then s1 again without pose.csv: pose.csv is gone, s2 untouched


def test_republishing_the_same_files_makes_no_commit(...):
    # publish s1 twice with identical files (same marker dict): one commit on main, no error


def test_publish_only_checks_out_its_own_session(...):
    # after publishing s1 and s2, publish s3 and assert the work dir has no sessions/s1


def test_published_files_reports_sizes_from_the_remote(...):
    # after publish: published_files("s1") == {".golf-etl.json": n, "session.md": m, ...} with
    # sizes equal to the source files


def test_a_rejected_push_raises(...):
    # make remote.git reject pushes (a pre-receive hook that exits 1): publish raises
    # subprocess.CalledProcessError or a RuntimeError naming the push
```

Fill each body from its comment; assertions compare exact sets and counts.

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/test_github_publisher.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'golf_etl.storage.github'`.

- [ ] **Step 3: Implement**

Each publish starts from an empty `work` dir: `git init -b main`, add `origin`, `git sparse-checkout set --no-cone /sessions/<id>/` (so no other session is checked out), `git fetch --depth 1 --filter=blob:none origin main`. A failed fetch whose stderr says the ref was not found means an empty repo: continue on an unborn `main`. Otherwise `git checkout -B main FETCH_HEAD`. Replace `sessions/<id>/`, write the marker as `json.dumps(marker, indent=2)`, `git add -A sessions/<id>`, skip commit and push when `git diff --cached --quiet` succeeds, else commit and `git push origin main`. All git calls set `GIT_SSH_COMMAND="ssh -i <key> -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=<known_hosts>"` when `ssh_key` is set, and `GIT_TERMINAL_PROMPT=0`. `published_files` fetches `main` again and reads `git ls-tree -r -l FETCH_HEAD sessions/<id>`.

`github_known_hosts`: GitHub's `ssh-ed25519`, `ecdsa-sha2-nistp256`, and `ssh-rsa` host keys for `github.com` from `https://api.github.com/meta` (`ssh_keys`). Before committing, check the ed25519 key's `ssh-keygen -lf` fingerprint is `SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU` as published at `https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints`.

- [ ] **Step 4: Run CI**

Run: `podman build --target test -t golf-etl:test .`
Expected: passes (the `test` stage has git once the apt line includes it).

- [ ] **Step 5: Commit**

```bash
git add src/golf_etl/storage/github.py src/golf_etl/storage/github_known_hosts pyproject.toml Dockerfile tests/test_github_publisher.py
git commit -s -m "feat: publish sessions to a github repo"
```

### Task 7: `session.md` describes what is in the folder

**Files:**
- Modify: `src/golf_etl/session.py` (`GUIDE` becomes `guide(storage: str) -> str`; `SessionSummary.storage: str = "github"`)
- Modify: `src/golf_etl/pipeline.py` (pass `cfg.storage`)
- Test: `tests/test_session.py`

**Interfaces:**
- Produces: `guide(storage)`. `github`: the same bullets as today minus the `-small.jpg` bullet and the `clip.mp4` bullet, the `pose.csv` bullet no longer mentions "the nearest `clip.mp4` frame" but says "the frame number in the swing", and the Timing bullet says "frame numbers counted from 2.5s before impact". `drive`: today's text exactly.

- [ ] **Step 1: Write the failing tests**

```python
def test_github_session_md_does_not_mention_clips_or_small_copies(tmp_path):
    # SessionSummary(..., storage="github") with one swing
    text = (tmp_path / "session.md").read_text()
    assert "clip.mp4" not in text and "-small.jpg" not in text and "Drive connector" not in text


def test_drive_session_md_keeps_the_drive_guide(tmp_path):
    # storage="drive"
    text = (tmp_path / "session.md").read_text()
    assert "06-sequence-small.jpg" in text and "`clip.mp4` is the swing as video." in text
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/test_session.py -k "github_session_md or drive_session_md" -q`
Expected: FAIL with `TypeError` on the unknown `storage` argument.

- [ ] **Step 3: Implement**

The ball flight guide's "following the ball in `clip.mp4`" becomes "following the ball in the swing video" for both modes.

- [ ] **Step 4: Run CI**

Run: `podman build --target test -t golf-etl:test .`
Expected: passes.

- [ ] **Step 5: Commit**

```bash
git add src/golf_etl/session.py src/golf_etl/pipeline.py tests/test_session.py
git commit -s -m "feat: describe only the published files in session.md"
```

### Task 8: `poll` with the storage switch, and `migrate-drive`

**Files:**
- Create: `src/golf_etl/storage/migrate.py`
- Modify: `src/golf_etl/cli.py`, `Dockerfile` (`CMD ["poll"]`)
- Test: `tests/test_cli.py`, `tests/test_migrate.py`

**Interfaces:**
- Consumes: `RcloneStore`, `DrivePublisher`, `GitHubPublisher`, `sweep_failed`, `FakeStore`.
- Produces:
  - CLI `poll`: `github` mode uses `RcloneStore(cfg.inbox_root)` and `GitHubPublisher(cfg.sessions_repo, Path(cfg.scratch_dir) / "golf-sessions", cfg.ssh_key_path)`; `drive` mode is today's. Any other `GOLF_STORAGE` exits 2 with `GOLF_STORAGE must be github or drive`. Github mode with no file at `ssh_key_path` exits 2 naming the path, unless `sessions_repo` starts with `file://`. `poll-drive` stays as an alias for `poll` (the deployed cronjob calls it; plan 3 switches the args).
  - CLI `migrate-drive [--dry-run]`.
  - `migrate.migrate(store: Store, publisher: GitHubPublisher, scratch: Path, dry_run: bool) -> MigrateReport` with `MigrateReport(copied: list[str], deleted: list[str], kept: list[str], blocked: list[str])`. Skips `clip.mp4` and `session.zip`; skips session folder names starting with `.`; `blocked` lists every entry in `inbox/`, `processing/`, `failed/` and, when non-empty, nothing is copied or deleted. A session is deleted from Drive only when `published_files` matches every downloaded file's name and size (the marker included); otherwise it goes in `kept`.

- [ ] **Step 1: Write the failing tests**

The two existing `poll-drive` tests in `tests/test_cli.py` get `monkeypatch.setenv("GOLF_STORAGE", "drive")`, since github is now the default.

```python
# tests/test_cli.py
def test_poll_rejects_an_unknown_storage(monkeypatch, capsys):
    monkeypatch.setenv("GOLF_STORAGE", "s3")
    assert main(["poll"]) == 2
    assert "GOLF_STORAGE must be github or drive" in capsys.readouterr().err


def test_github_poll_without_a_deploy_key_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GOLF_SSH_KEY_PATH", str(tmp_path / "missing"))
    assert main(["poll"]) == 2
    assert str(tmp_path / "missing") in capsys.readouterr().err


@pytest.mark.media
def test_github_poll_publishes_to_the_repo_and_empties_the_inbox(tmp_path, monkeypatch):
    # bare repo with allowFilter as in Task 6; GOLF_INBOX_ROOT=tmp_path/data with an inbox/ video
    # from make_video_with_clicks; GOLF_SESSIONS_REPO=file://...; GOLF_SCRATCH_DIR set
    assert main(["poll"]) == 0
    # one sessions/<id>/session.md in the bare repo's main; inbox has no video


# tests/test_migrate.py (FakeStore with sessions/s1/{session.md,.golf-etl.json,swing-01/01-address.jpg,
# swing-01/clip.mp4,session.zip}, bare repo publisher)
def test_migration_copies_everything_but_clips_and_zips_then_deletes_from_drive(...)
def test_dry_run_copies_and_deletes_nothing(...)
def test_migration_refuses_while_the_inbox_or_failed_folder_has_files(...)
def test_migration_keeps_a_drive_session_whose_push_is_missing_a_file(...):
    # monkeypatch publisher.published_files to drop one file: report.kept == ["s1"], s1 still in store
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/test_cli.py tests/test_migrate.py -q`
Expected: FAIL (`invalid choice: 'poll'`, missing `golf_etl.storage.migrate`).

- [ ] **Step 3: Implement**

`migrate` downloads each session's files with `store.download` into `scratch/<id>/`, reads the existing marker JSON from `.golf-etl.json` and passes it as `marker` (so the original publish time and pipeline version survive), publishes, verifies, deletes. `cmd_migrate` prints one line per session and a final count, and returns 1 when anything is `blocked` or `kept`.

- [ ] **Step 4: Run CI**

Run: `podman build --target test -t golf-etl:test .`
Expected: passes.

- [ ] **Step 5: Commit**

```bash
git add -A src tests Dockerfile
git commit -s -m "feat: switch storage with GOLF_STORAGE and migrate drive sessions to github"
```

### Task 9: Size images for the GitHub budget

**Files:**
- Modify: `src/golf_etl/config.py` (`frame_long_edge`, `jpeg_quality` defaults only if the measurement says so)
- Test: `tests/test_config.py`

- [ ] **Step 1: Measure the current size per swing**

Process `local/IMG_4457.MOV` (712s range session) in the image with `golf-etl process` into the scratchpad. Sum, per swing, the bytes of `05-impact-zoom.jpg`, `06-sequence.jpg`, `07-after-shot.jpg`, `ball.json`, `pose.csv`. Record the mean in the ledger.

- [ ] **Step 2: Pick settings**

If the mean is at most 1,000,000 bytes, keep the defaults. Otherwise re-encode that session's three full-size images from their sources with `render.fit` and `render.save_jpeg` at `jpeg_quality` 85, 80, 75 and `frame_long_edge` 2560 then 2048, and take the first pair (highest quality, then larger edge) whose mean is at most 1,000,000 bytes. Check legibility at that pair by viewing `06-sequence.jpg` (red keypoints, ball, shaft visible) and re-encoding Drive session `2026-10-16-1251-a2e4c6fd` `swing-01/07-after-shot.jpg` the same way (every launch monitor number readable, including back spin `4728`). Ledger the chosen pair, the mean, and what was checked.

- [ ] **Step 3: Pin the choice in a test, then change the defaults**

```python
def test_frame_defaults_fit_the_github_budget():
    s = Settings()
    assert (s.frame_long_edge, s.jpeg_quality) == (<edge>, <quality>)
```
Run it to see it fail (unless the defaults were kept, in which case skip this step and ledger why), change `config.py`, add a comment above the two fields naming the budget (about 1MB per swing so a session of 30 swings is about 30MB), run CI.

- [ ] **Step 4: Commit**

```bash
git add src/golf_etl/config.py tests/test_config.py
git commit -s -m "feat: size full frames to keep a session near 30MB on github"
```

### Task 10: Docs

**Files:**
- Modify: `docs/design.md`, `README.md`

- [ ] **Step 1: Rewrite to describe what is true now**

`docs/design.md`: intro and Flow describe github mode (upload to the homelab inbox folder, `poll` every 3 minutes, sessions pushed to `golf-sessions`, review through the claude.ai GitHub integration); a short "Drive mode" section says `GOLF_STORAGE=drive` keeps the Drive inbox and Drive sessions with the TTL and size cap. Update the file tree (github mode: no `clip.mp4`, no `-small.jpg`), Goals ("keep Drive usage bounded" only applies to drive mode), the storage, retention, and runtime contract sections (inbox layout `inbox/`, `processing/`, `failed/`, `.golf-etl/state.json` under `GOLF_INBOX_ROOT`, the `recent` status list from Task 5, the deploy key and pinned host keys), the CLI (`poll`, `migrate-drive`), and the time format. `README.md`: same flow in brief and the new settings. No history of what changed.

- [ ] **Step 2: Check**

Run: `grep -nP '[\x{2013}\x{2014}]' docs/design.md README.md || echo none`
Expected: `none`.

- [ ] **Step 3: Commit**

```bash
git add docs/design.md README.md
git commit -s -m "docs: describe github storage as the default"
```

### Task 11: The skill reads sessions from GitHub

**Files:**
- Modify: `claude/skills/golf-swing-analysis/SKILL.md`

- [ ] **Step 1: Rewrite Step 1**

Frontmatter `description` mentions golf-etl sessions in the `golf-sessions` GitHub repo and still Google Drive. Step 1 sources become:
- **1A: From the `golf-sessions` GitHub repo** (default). If the repo is not in the chat, ask the owner to add it with **+ > Add from GitHub > golf-sessions**. List `sessions/`, newest name is the latest. Read `session.md` first, then the full-size `06-sequence.jpg`, `05-impact-zoom.jpg`, `07-after-shot.jpg` (no `-small` copies here). Older sessions moved from Drive have `01-address.jpg` to `04-finish.jpg` and may lack `pose.csv` and the Timing table; use what is there. Keep today's steps 4 to 10 (choosing swings, reading order, numbers, pose.csv, trust the photo, look across swings).
- **1B: From Google Drive** (drive mode): today's 1A, unchanged.
- **1C: From an uploaded video**: today's 1B.

Add to the output rules: whenever a swing is named, give the source video and the time to scrub to in Photos from the swing's Impact time in `session.md`, for example "`swing-07`, scrub to 12:07 in `IMG_4601`". The owner checks Claude's read against the original that way; there are no clips.

- [ ] **Step 2: Check**

Run: `grep -nP '[\x{2013}\x{2014}]' claude/skills/golf-swing-analysis/SKILL.md || echo none`
Expected: `none`.

- [ ] **Step 3: Commit and zip for the owner**

```bash
git add claude/skills/golf-swing-analysis/SKILL.md
git commit -s -m "feat(skill): read sessions from the golf-sessions repo"
```
Zip `claude/skills/golf-swing-analysis/` as `golf-swing-analysis.zip` in the scratchpad and send it to the owner with SendUserFile.

### Task 12: Live check, review, PR

**Interfaces:**
- Consumes: Task 1 merged and live.

- [ ] **Step 1: Deploy key (stop and ask the owner first)**

```bash
ssh-keygen -t ed25519 -N "" -C golf-etl -f local/golf-sessions-deploy-key
gh repo deploy-key add local/golf-sessions-deploy-key.pub --repo rumstead/golf-sessions --allow-write --title golf-etl
```
`local/` is git-ignored. Plan 3 encrypts the private key into the homelab SOPS secret and deletes the local copy.

- [ ] **Step 2: Publish a real session from this machine**

Build the image (`podman build -t golf-etl:dev .`), put `local/IMG_4439-a.mov` in a scratch `data/inbox/`, and run `poll` in the container with `GOLF_STORAGE=github`, `GOLF_INBOX_ROOT=/data`, the deploy key mounted read-only at `/etc/golf-etl/ssh/id_ed25519`, and `--user root` for the mounts.
Expected: exit 0; `gh api repos/rumstead/golf-sessions/contents/sessions` lists one session; its tree has no `clip.mp4` or `-small.jpg`; `data/inbox/` is empty; `data/.golf-etl/state.json` has a `published` item.

- [ ] **Step 3: Owner reviews it in claude.ai**

Owner uploads the skill zip from Task 11, opens a chat on claude.ai in a browser, adds `golf-sessions` with **+ > Add from GitHub**, and asks "review my latest golf session". Expected: the skill finds the session, reads the full-size images, and gives a scrub time for any swing it names.

- [ ] **Step 4: Final review, push, PR**

Whole-branch review as the executing skill says. Then push `feat/github-sessions` and open a PR in the owner's voice. Do not merge until Task 1's ConfigMap value is confirmed live and the owner says to.
