# Upload Page, Cutover, and Drive Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The owner uploads range videos from iPhone Safari over the tailnet to a resumable upload page in the homelab, the cronjob runs in github mode against that volume, and the five Drive sessions move to `golf-sessions` and leave Drive.

**Architecture:** One `golf-etl-upload` pod on the worker, behind a Tailscale Ingress (`golf-upload`): nginx in front, `tusd` for the resumable tus protocol (files land in `/data/uploads/`), and `golf-etl upload-server` for the page, a `/status` JSON, and tusd's hooks (a finished upload is moved into `/data/inbox/` under its original name). The cronjob mounts the same volume and runs `poll` with `GOLF_STORAGE=github` and the deploy key. The migration runs once from this machine with `migrate-drive`.

**Tech Stack:** Python 3.12 stdlib `http.server` (no new dependencies), tusd `v2.10.1`, tus-js-client `4.3.1` from jsdelivr, nginx unprivileged, Kubernetes, Argo CD, KSOPS, Tailscale operator, OpenSpec CLI `1.14.0`.

**Spec:** `docs/superpowers/specs/2026-10-17-github-sessions-design.md` (Upload, Retention at home, Migration, Owner's manual steps)

## Global Constraints

- The page delivers the original file byte for byte with its original name. Picking from Photos is preferred; the accepted fallback is Save to Files then pick from Files.
- A video only appears in `/data/inbox/` once it is complete. Uploads that never finish are deleted after 1 day.
- Resuming after Safari reloads: picking the same video again continues (tus-js-client's default fingerprint of name, size, and modified time, stored in the browser).
- The page lists recent uploads with status: uploading, waiting, processing, published (with session id), failed (with the reason). Statuses come from `.golf-etl/state.json` `recent` (written by `poll`; `updated` is ISO 8601 with `+00:00`) plus the inbox and upload folders.
- Tailnet only, through the Tailscale Ingress with HTTPS on its `ts.net` name. No other auth. No Funnel.
- tusd's hook endpoint is reachable only inside the pod.
- Upload pod and cronjob share one `local-path` ReadWriteOnce PVC and are pinned to `talos-hk3-kam`. All containers run as uid 10001 with fsGroup 10001.
- The rclone secret stays mounted so `GOLF_STORAGE=drive` is a one-line rollback (plus `poll` keeps working in drive mode).
- golf-etl commits: `<type>: <subject>`, subject only, `git commit -s`, no AI attribution; PRs and code comments follow `writing-as-rumstead`. No em or en dashes. Docs describe what is true now. golf-etl CI is `podman build --target test -t golf-etl:test .`.
- Homelab changes go through OpenSpec (`openspec/AGENTS.md`), validated with `podman run --rm -e OPENSPEC_TELEMETRY=0 -v "$PWD":/w:Z -w /w docker.io/library/node:22-slim npx -y @fission-ai/openspec@1.14.0 validate <id> --strict`. Homelab commits `<type>(golf-etl): <subject>`. Squash merges. Every merge in either repo waits for the owner.
- Secrets are committed only as `*.sops.yaml` and never printed.

## Review Focus

1. Two uploads with the same name (`IMG_4601.MOV` from two days) must both reach the inbox: Task 1 adds `test_finished_upload_with_a_taken_name_gets_a_suffix`.
2. A file name with path parts (`../../etc/x.mov`) must not escape the inbox: Task 1 adds `test_finished_upload_name_cannot_leave_the_inbox`.
3. A post-finish hook that never arrives (tusd restarted) must still get the video into the inbox: Task 1 adds `test_sweep_moves_a_complete_upload_whose_hook_was_missed`.
4. An upload in progress for longer than the TTL is not deleted while its lock file exists: Task 1 adds `test_sweep_keeps_an_old_upload_that_is_still_locked`.
5. The deploy key read by uid 10001 from a Secret volume: ssh must accept its permissions. Task 5 Step 4 checks it in the cluster before the cutover is called done.

---

### Task 0: Renovate for golf-etl (golf-etl)

**Files:**
- Create: `.github/renovate.json5`

- [ ] **Step 1: Write the config**

Same shape and voice as `/home/rumstead/homelab/.github/renovate.json5`: `extends: ['config:recommended', ':dependencyDashboard']`, `timezone: 'America/New_York'`, `schedule: ['before 6am on monday']`, `prConcurrentLimit: 5`, `labels: ['dependencies']`. Coverage:
- `pip_requirements` for `requirements.txt` and `requirements-dev.txt` (default file patterns).
- `dockerfile` for the `python:3.12-slim` base (default).
- `github-actions` for `.github/workflows/ci.yaml` (default).
- A regex custom manager for `ARG GOLF_RCLONE_VERSION=v1.75.1` in `Dockerfile`, datasource `github-releases`, depName `rclone/rclone`.
- A regex custom manager for `tus-js-client@<version>` in `src/golf_etl/upload_page/index.html` (it arrives in Task 1), datasource `npm`, depName `tus-js-client`.

Package rules: patch updates automerge once CI passes (`automerge: true`, `matchUpdateTypes: ['patch']`); everything else opens a PR for the owner; non-major pip updates grouped as `python dependencies`; the Python base image's minor (3.12 to 3.13) needs dependency dashboard approval, since MediaPipe and OpenCV wheels have to exist for it. A comment at the top says what the file covers, like homelab's. Homelab's existing config already covers the tusd, nginx, and busybox image tags added in Task 3 through its `kubernetes` manager.

- [ ] **Step 2: Validate**

```bash
podman run --rm -v "$PWD/.github/renovate.json5":/c.json5:ro,Z -e RENOVATE_CONFIG_FILE=/c.json5 docker.io/renovate/renovate:<tag at least two weeks old> renovate-config-validator /c.json5
```
Expected: `Config validated successfully`.

- [ ] **Step 3: Commit, PR, owner merges**

```bash
git checkout -b chore/renovate
git add .github/renovate.json5
git commit -s -m "chore: keep dependencies updated with renovate"
```

- [ ] **Step 4: Owner gives the Renovate app access to golf-etl** (GitHub, Settings, Applications, Renovate, Repository access, add `golf-etl`). Expected: a `Dependency Dashboard` issue appears on `rumstead/golf-etl` within an hour.

### Task 1: `golf-etl upload-server` (golf-etl)

**Files:**
- Create: `src/golf_etl/upload.py`
- Create: `src/golf_etl/upload_page/index.html` (package data, same mechanism as `github_known_hosts`)
- Modify: `src/golf_etl/config.py` (`upload_dir: str = "/data/uploads"`, `upload_ttl_hours: int = 24`, `upload_port: int = 8090`, under the storage group; nginx owns 8080 in the pod)
- Modify: `src/golf_etl/cli.py` (`upload-server`)
- Test: `tests/test_upload.py`

**Interfaces:**
- Produces:
  - `finish_upload(upload: Path, filename: str, inbox: Path) -> Path`: moves the tusd data file into `inbox` (same filesystem, `os.replace`) under `safe_name(filename)`, adding `-2`, `-3` before the suffix when the name is taken; removes `<upload>.info`; returns the new path.
  - `safe_name(filename: str) -> str`: the last path component only, control characters and `/` `\` removed, leading dots stripped; empty becomes `upload.mov`.
  - `sweep_uploads(upload_dir: Path, inbox: Path, now: datetime, ttl: timedelta) -> list[str]`: for each `<id>.info` (tusd filestore JSON with `Size` and `MetaData.filename`): if the data file's size equals `Size` and no `<id>.lock` exists, `finish_upload` it; else if the data file's mtime is older than `ttl` and no `<id>.lock` exists, delete the data file and `.info`. Returns the names it moved or deleted.
  - `status(inbox_root: Path, upload_dir: Path) -> dict`: `{"uploading": [{"name", "offset", "size"}], "waiting": [{"name", "size"}], "recent": <State.recent>}`. `waiting` is the videos in `inbox/` only; a video in `processing/` already has a `processing` item in `recent`.
  - `hook_response(payload: dict, cfg: Settings) -> dict`: tusd HTTP hook handling. `pre-create` with a filename whose suffix is not in `VIDEO_SUFFIXES` returns `{"RejectUpload": true, "HTTPResponse": {"StatusCode": 400, "Body": "only videos"}}`; `post-finish` with `Event.Upload.Storage.Path` calls `finish_upload` and returns `{}`; anything else returns `{}`.
  - `serve(cfg: Settings) -> None`: `ThreadingHTTPServer` on `127.0.0.1:cfg.upload_port`: `GET /` the page, `GET /status` JSON, `POST /hooks` JSON to `hook_response`. A daemon thread runs `sweep_uploads` every 10 minutes. CLI `golf-etl upload-server` calls it.

- [ ] **Step 1: Write the failing tests**

```python
def test_finished_upload_moves_into_the_inbox_under_its_name(tmp_path): ...
    # upload dir has <id> (b"video") and <id>.info; finish_upload(..., "IMG_4601.MOV", inbox)
    # -> inbox/IMG_4601.MOV with b"video", <id> and <id>.info gone

def test_finished_upload_with_a_taken_name_gets_a_suffix(tmp_path):
    # inbox already has IMG_4601.MOV -> returns inbox/IMG_4601-2.MOV; a third -> IMG_4601-3.MOV

def test_finished_upload_name_cannot_leave_the_inbox(tmp_path):
    # filename "../../etc/x.mov" -> inbox/x.mov; "" -> inbox/upload.mov; ".hidden.mov" -> inbox/hidden.mov

def test_sweep_moves_a_complete_upload_whose_hook_was_missed(tmp_path):
    # <id> size == info Size, no lock -> moved, name in the returned list

def test_sweep_deletes_an_unfinished_upload_after_the_ttl(tmp_path):
    # size < Size, mtime 25h ago, no lock -> both files gone

def test_sweep_keeps_a_young_unfinished_upload(tmp_path):
    # size < Size, mtime 1h ago -> untouched

def test_sweep_keeps_an_old_upload_that_is_still_locked(tmp_path):
    # size < Size, mtime 25h ago, <id>.lock present -> untouched

def test_status_lists_uploading_waiting_and_recent(tmp_path):
    # one partial upload, one video in inbox/, state.json with one recent item -> exact dict

def test_pre_create_hook_rejects_non_videos():
    assert hook_response({"Type": "pre-create", "Event": {"Upload": {"MetaData": {"filename": "a.zip"}}}}, Settings())["RejectUpload"] is True

def test_post_finish_hook_moves_the_upload(tmp_path):
    # payload shaped like tusd's docs (Type post-finish, Event.Upload.ID, MetaData.filename,
    # Storage.Path) -> file in inbox, response {}

def test_server_serves_the_page_status_and_hooks(tmp_path):
    # start serve() on port 0 in a thread (or the handler on a test server); GET / contains
    # "tus.Upload", GET /status is JSON with the three keys, POST /hooks returns {}
```
Write each body out with exact assertions from its comment.

- [ ] **Step 2: Run them to see them fail**

Run: `podman build --target test -t golf-etl:test .` (or the focused pytest in the test image)
Expected: FAIL with `ModuleNotFoundError: No module named 'golf_etl.upload'`.

- [ ] **Step 3: Implement `upload.py`, the CLI command, and `index.html`**

`index.html` (one file, no build step, plain CSS that reads on a phone): loads `https://cdn.jsdelivr.net/npm/tus-js-client@4.3.1/dist/tus.min.js`; a file input `accept="video/*" multiple`; for each file a `tus.Upload` with `endpoint: "/files/"`, `chunkSize: 50 * 1024 * 1024`, `retryDelays: [0, 3000, 10000, 30000, 60000]`, `metadata: {filename: file.name, filetype: file.type}`, `removeFingerprintOnSuccess: true`, and `findPreviousUploads()` then `resumeFromPreviousUpload` before `start()`; a progress bar per file; a line saying to keep the page open until each says done, and that if the phone shows "Compressing" or the upload fails to keep the original, save the video to Files and pick it with Browse. A status list below polls `/status` every 10 seconds and shows uploading (percent), waiting, processing, published (session id), failed (error). No other external requests.

- [ ] **Step 4: Run CI**

Run: `podman build --target test -t golf-etl:test .`
Expected: passes.

- [ ] **Step 5: Commit, PR, owner merges**

```bash
git checkout -b feat/upload-server
git add src/golf_etl/upload.py src/golf_etl/upload_page src/golf_etl/config.py src/golf_etl/cli.py pyproject.toml tests/test_upload.py
git commit -s -m "feat: serve a resumable upload page for the inbox"
```
Also add a short "Upload page" section to `docs/design.md` and a line to `README.md` describing what is true after this merge (commit `docs: describe the upload page`). Push, open the PR, wait for CI and the owner, merge, and wait for `main`'s image build.

### Task 2: Homelab change proposal

**Files (homelab):**
- Create: `openspec/changes/update-golf-etl-github-storage/{proposal.md,tasks.md,specs/golf-etl/spec.md}`

- [ ] **Step 1: Write the change**

`proposal.md` in the shape of the archived `add-golf-etl` one: Why (Drive quota, connector image limit, rclone client retirement; golf-etl now has a github mode), What Changes (shared PVC `golf-etl-data`, upload pod and Tailscale Ingress `golf-upload`, deploy key Secret, cronjob in github mode running `poll`, rclone secret kept for `GOLF_STORAGE=drive` rollback), Impact. `specs/golf-etl/spec.md`: `## ADDED Requirements` for Upload Page (tailnet-only HTTPS, resumable, finished uploads only in the inbox), Shared Volume (one PVC, both workloads on the worker), Deploy Key (SOPS, write access to `golf-sessions` only); `## MODIFIED Requirements` for Scheduled Pipeline Runs (`golf-etl poll`, not `poll-drive`) and Drive Credentials (kept for drive mode; github mode uses the deploy key), copying the full current requirement text from `openspec/specs/golf-etl/spec.md` before editing. Scenarios for each. `tasks.md` lists Tasks 3 to 7 of this plan.

- [ ] **Step 2: Validate and commit on branch `feat/golf-etl-github-storage`**

Expected: `Change 'update-golf-etl-github-storage' is valid`. Commit `docs(golf-etl): propose moving the pipeline to github storage`.

### Task 3: Shared volume and upload pod (homelab)

**Files (homelab):**
- Create: `kubernetes/manifests/golf-etl/pvc.yaml` (`golf-etl-data`, 50Gi, RWO)
- Create: `kubernetes/manifests/golf-etl/upload.yaml` (Deployment `golf-etl-upload`, Service `golf-etl-upload`, Ingress `golf-upload`)
- Create: `kubernetes/manifests/golf-etl/upload-nginx.conf` plus a `configMapGenerator` entry in `kustomization.yaml`
- Modify: `kubernetes/manifests/golf-etl/kustomization.yaml`, `AGENTS.md` if it lists golf-etl resources

**Interfaces:**
- Produces: PVC `golf-etl-data` mounted at `/data` (tusd writes `/data/uploads/`, the cronjob reads `/data/inbox/`), `https://golf-upload.tailb4b203.ts.net`.

- [ ] **Step 1: Write the manifests**

Deployment, 1 replica, `strategy: Recreate`, `nodeSelector: kubernetes.io/hostname: talos-hk3-kam`, pod `securityContext` `runAsUser: 10001`, `runAsGroup: 10001`, `fsGroup: 10001`, `runAsNonRoot`, `seccompProfile: RuntimeDefault`; every container `allowPrivilegeEscalation: false`, drops ALL; small requests (50m, 64Mi each) and limits (tusd 512Mi, others 256Mi).
- `nginx`: `nginxinc/nginx-unprivileged` pinned to a 1.29.x alpine tag at least two weeks old, port 8080 named `http`, config from the generated ConfigMap: `client_max_body_size 0; proxy_request_buffering off; proxy_buffering off; proxy_http_version 1.1;` `location /files/` to `http://127.0.0.1:8081` with `Host`, `X-Forwarded-Host`, `X-Forwarded-Proto https`; `location = /hooks { return 404; }`; `location /` to `http://127.0.0.1:8090`.
- `tusd`: `tusproject/tusd:v2.10.1`, args `-host 127.0.0.1 -port 8081 -base-path /files/ -upload-dir /data/uploads -behind-proxy -hooks-http http://127.0.0.1:8090/hooks -hooks-enabled-events pre-create,post-finish -hooks-http-retry 5` and the backoff flag in the form `tusd -help` shows for v2.10.1. Mount the PVC at `/data`.
- `upload-server`: `ghcr.io/rumstead/golf-etl:latest`, `imagePullPolicy: Always`, args `["upload-server"]`, env `GOLF_INBOX_ROOT=/data`, `GOLF_UPLOAD_DIR=/data/uploads`, PVC at `/data`.

An initContainer (busybox pinned, same uid) runs `mkdir -p /data/uploads /data/inbox /data/processing /data/failed /data/.golf-etl`.

Service `golf-etl-upload` port 80 to `http`. Ingress `golf-upload`: `ingressClassName: tailscale`, `defaultBackend` the Service, `tls: [{hosts: [golf-upload]}]`.

- [ ] **Step 2: Render**

The local kustomize cannot run KSOPS, so dry-run the new files one by one with `kubectl apply --dry-run=client -f <file>`, and render the nginx ConfigMap with `kubectl create configmap golf-etl-upload-nginx --from-file=default.conf=kubernetes/manifests/golf-etl/upload-nginx.conf --dry-run=client -o yaml`.
Expected: every object `created (dry run)`.

- [ ] **Step 3: Commit, push, PR (Tasks 2 and 3 together), owner merges, sync**

Commit `feat(golf-etl): add the upload page and shared volume`. After merge: Argo CD `golf-etl` Synced and Healthy; `kubectl -n golf-etl get pods -l app=golf-etl-upload` has 3/3 Running; `kubectl -n golf-etl get ingress golf-upload` shows `golf-upload.tailb4b203.ts.net`. The cronjob is still in drive mode and ignores `/data`.

### Task 4: The iPhone sends the original (owner)

- [ ] **Step 1: Owner uploads one short range video from Photos** at `https://golf-upload.tailb4b203.ts.net` and notes its size and resolution from Photos' Info panel.

- [ ] **Step 2: Compare**

```bash
kubectl -n golf-etl exec deploy/golf-etl-upload -c upload-server -- sh -c 'ls -l /data/inbox && for f in /data/inbox/*; do ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate:format=size,duration:format_tags=creation_time,com.apple.quicktime.model -of compact "$f"; done'
```
Expected: the original name (e.g. `IMG_4612.MOV`), HEVC at the Photos resolution and 120/1 frame rate, the same byte size as Photos shows, `com.apple.quicktime.model` present. If any differ, the owner repeats with Save to Files then Browse, and the result decides which route the page text recommends first (adjust `index.html` in a small golf-etl PR if Files is the one that works).

- [ ] **Step 3: Clear the test file** with `kubectl -n golf-etl exec deploy/golf-etl-upload -c upload-server -- rm /data/inbox/<name>`, after the owner confirms it.

### Task 5: Cutover (homelab)

**Files (homelab):**
- Create: `kubernetes/manifests/golf-etl/golf-etl-deploy-key.sops.yaml` (Secret `golf-etl-deploy-key`, key `id_ed25519`)
- Modify: `kubernetes/manifests/golf-etl/ksops-generator.yaml`, `configmap.yaml` (`GOLF_STORAGE: github`, `GOLF_INBOX_ROOT: /data`), `cronjob.yaml`

- [ ] **Step 1: Encrypt the deploy key**

From `/home/rumstead/golf-etl/local/golf-sessions-deploy-key`, write the Secret YAML to the homelab path and `sops encrypt -i` it in one command that never prints the key. Check `grep -c 'BEGIN OPENSSH' kubernetes/manifests/golf-etl/golf-etl-deploy-key.sops.yaml` prints `0`.

- [ ] **Step 2: Change the cronjob**

`args: ["poll"]`; `nodeSelector: kubernetes.io/hostname: talos-hk3-kam`; volumes `data` (PVC `golf-etl-data`) at `/data` and `deploy-key` (Secret `golf-etl-deploy-key`, `defaultMode: 0400`, items `id_ed25519`) at `/etc/golf-etl/ssh` read-only; keep `envFrom` for the rclone secret. Update the comment at the top of the file to say what is true now.

- [ ] **Step 3: OpenSpec validate, commit, PR, owner merges, sync**

Commit `feat(golf-etl): run the pipeline in github mode`.

- [ ] **Step 4: Check the first run**

```bash
kubectl -n golf-etl create job golf-etl-cutover-check --from=cronjob/golf-etl-poll
kubectl -n golf-etl wait --for=condition=complete job/golf-etl-cutover-check --timeout=10m
kubectl -n golf-etl logs job/golf-etl-cutover-check | tail -5
```
Expected: exit 0, a `poll:` line, no ssh `bad permissions` or host key errors (with an empty inbox there is no push; if ssh never runs, upload the short test video again from the phone and let the next scheduled run publish it). If ssh rejects the key's permissions, change `defaultMode` to `0440` or `0600`, whichever ssh accepts for a root-owned file read through fsGroup, and repeat. Delete the check job afterwards.

- [ ] **Step 5: End to end from the phone**

Owner uploads one short video from the phone. Expected within a few minutes: the page shows it waiting, then processing, then published with its session id; `gh api repos/rumstead/golf-sessions/contents/sessions` lists that session.

### Task 6: Drive migration (from this machine)

- [ ] **Step 1: Owner confirms nothing new is going to Drive** (the phone uses the upload page now).

- [ ] **Step 2: Dry run**

```bash
cd /home/rumstead/golf-etl && podman run --rm --user root --env-file local/drive.env -e GOLF_SCRATCH_DIR=/scratch -v <scratch>:/scratch:Z -v $PWD/local/golf-sessions-deploy-key:/etc/golf-etl/ssh/id_ed25519:ro,Z ghcr.io/rumstead/golf-etl:latest migrate-drive --dry-run
```
Expected: the five sessions (`2026-10-02` to `2026-10-16-1251-a2e4c6fd`) listed to copy and delete, nothing blocked. If anything is blocked, stop and show the owner the list.

- [ ] **Step 3: Owner says go, then the real run**

Same command without `--dry-run`. Expected: exit 0, five copied, five deleted, none kept. Then `rclone lsf gdrive:golf/sessions/` (with drive.env) is empty and `gh api repos/rumstead/golf-sessions/contents/sessions` lists the five plus the new sessions.

### Task 7: Clean up

- [ ] **Step 1: Delete the local plaintext deploy key** (`local/golf-sessions-deploy-key` and `.pub`) once Task 5 Step 5 and Task 6 have passed; the cluster's SOPS copy is the only one.
- [ ] **Step 2: Delete the spike branch** `spike/golf-connector-test` in `rumstead/weekly-notes` (`gh api -X DELETE repos/rumstead/weekly-notes/git/refs/heads/spike/golf-connector-test`), after asking the owner.
- [ ] **Step 3: Archive the OpenSpec change** in its own homelab PR, the same way as `add-tailscale-operator` (tick `tasks.md`, `openspec archive ... --yes`, fill the spec's Purpose if it is a placeholder).
- [ ] **Step 4: Update memory** `drive-to-github-migration.md` to say the migration is done, with the upload URL.
