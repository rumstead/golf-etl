# Move golf-etl off Google Drive

## Intent

Today a range video goes from the iPhone to a Google Drive inbox, the homelab cronjob turns it into a session folder in Drive, and Claude reads the session through the claude.ai Drive connector. Three things make that painful:

- Uploading a multi-GB video to Drive from the phone is slow and needs retries.
- The claude.ai Drive connector cannot return images over about 72KB, which forced the `-small.jpg` copies and, before them, zipping sessions and uploading them by hand.
- Drive shares a 15GB free quota with Gmail and Photos, and rclone's shared Drive client ID is being retired during 2026. Replacing it needs a Google Cloud project, which the owner has tried before and does not want to do again.

The owner cares about how easy the two ends are (getting the video off the phone, and Claude reading the session). Processing time does not matter within reason.

The new flow:

```
iPhone (Tailscale app) --Safari upload page, resumable--> homelab volume inbox/
                                                                 |
                         golf-etl cronjob (every 3 minutes, never overlapping)
                           recover, claim, process, publish, delete original, sweep
                                                                 |
                              review files pushed to github.com/rumstead/golf-sessions (private)
                                                                 |
claude.ai in a browser: + > Add from GitHub > golf-sessions, "review my latest session"
```

## What was verified

- The claude.ai GitHub integration reads images from a private repo, including a 1.1MB 2040x1812 `06-sequence.jpg` that the Drive connector could not open. It found files on a non-default branch by itself. It described the panels, labels, golfer, and skeleton accurately. Test files are on the `spike/golf-connector-test` branch of `rumstead/weekly-notes`.
- The GitHub integration is available on the claude.ai website (including Safari on the iPhone). The owner did not find it in the iPhone app. Reviews start in a browser.
- GitHub does not play an `.mp4` in a private repo in iPhone Safari; it has to be saved first. Clips are not published.
- `rumstead/golf-sessions` exists, is private, and is empty.
- Tailscale's Kubernetes operator and Taildrop are both on the free Personal plan.
- The owner records range sessions in regular Video mode at 120fps, not Slo-mo (sample `IMG_4457.MOV`: HEVC 1920x1080 at 120fps, 712.9s, iPhone 17 Pro). Photos plays these at normal speed, so a time in the file is the same time in the Photos scrubber.
- The homelab worker `talos-hk3-kam` has about 120GB free on its disk; the control plane node has about 3GB. Storage is `local-path` (ReadWriteOnce, node-local).
- The homelab does not run Tailscale yet. Its Hubble design notes say "revisit when Tailscale lands".

## Goals

- Upload from the iPhone over cell data or wifi without Google, and survive a dropped connection without starting over.
- Claude reads a session, at full image detail, through the GitHub integration with no manual zipping or uploading.
- Storage is the owner's: homelab disk for videos in flight, a private GitHub repo for review files.
- Drive stays available as a fallback behind one setting, default off.
- The five existing Drive sessions move to GitHub and are deleted from Drive.

## Non-goals

- Keeping clips or a video viewer. The owner checks Claude's read by scrubbing the original video in Photos to the time Claude gives.
- The iPhone Claude app. Reviews start on claude.ai in a browser.
- Background uploads with Safari closed. The owner accepted keeping the upload page on screen. Switching away pauses the upload, it does not lose it.
- Any paid Tailscale feature.
- A public endpoint into the homelab.

## Anti-goals

These would make it a failure even if it technically works:

- Any credential that quietly expires and stops uploads or publishing (for example a personal Tailscale node key on its 180 day default, or a GitHub token with an expiry).
- Any Google Cloud project setup.
- The upload delivering something other than the original video. iOS can transcode or render slow motion when a video is picked from Photos in a browser. The audio onsets, the 120fps frame timing, and the capture time in the video metadata all depend on the original file.
- A partly uploaded video being picked up by the cronjob.

## Decisions the owner made

### Storage switch

One setting, `GOLF_STORAGE`, picks the whole mode:

- `github` (default): videos come from the homelab volume inbox, sessions are pushed to `golf-sessions`.
- `drive`: exactly today's behavior, Drive inbox and Drive sessions.

The code has two seams: where videos come from (`Inbox`: homelab folder or Drive folder) and where sessions go (`Publisher`: GitHub or Drive). Processing only talks to those two. The existing `Drive` protocol, `RcloneDrive`, and `tests/fake_drive.py` stay and back the `drive` mode. Mixed modes (Drive inbox with GitHub sessions) are not needed.

The rclone secret stays in the cluster so `drive` keeps working. The `drive` fallback only works until rclone's shared client ID is retired; that is accepted.

### Upload

A small web page on the tailnet, reached from Safari on the iPhone with the Tailscale app connected:

- Pick one or more videos, see per-file progress, and see when each is done.
- Resumable, chunked uploads. Reopening the page after a dropped connection or after Safari was backgrounded continues from where it stopped.
- A video only appears in the inbox once it is complete.
- The page must deliver the original file byte for byte, with its original name (for example `IMG_4601.MOV`). iOS hands the page a re-encoded copy when a video is picked from Photos, so the owner saves the video to Files first and picks it from Files on the page; checked with a real upload, which arrived byte for byte. Which route works is checked on the iPhone with a real video before the rest of the upload work is built.
- Served through the Tailscale Kubernetes operator as a tagged device, with HTTPS on the `ts.net` name. Tailnet membership is the only access control.

### Tailscale in the homelab

Tailscale is deployed as its own homelab change, before the upload service:

- The Tailscale Kubernetes operator, installed as one multi-source Argo CD Application (the official Helm chart plus a manifests folder with the namespace and a SOPS secret through ksops), the same way `kube-prometheus-stack` is.
- Set up as general homelab infrastructure, so other services can be put on the tailnet later. The upload service is its first user.
- Free Personal plan features only.

### Review files on GitHub

- Each session is pushed to `golf-sessions` under `sessions/<session-id>/`, same session ids as today.
- Published: `session.md`, and per swing `06-sequence.jpg`, `05-impact-zoom.jpg`, `07-after-shot.jpg`, `ball.json`, `pose.csv`. Not published: `clip.mp4` and the original video.
- Review files are never deleted from GitHub. There is no TTL or size cap on the repo.
- Reprocessing a video (re-uploading it) replaces its session folder in a new commit.

### Times in the video

Every time a swing is pointed at, it can be found in Photos. In `session.md` and image labels, times of a minute or more are shown as minutes and seconds (`12:07.4`), not `727.40s`. The skill, whenever it names a swing, gives the source video and the time to scrub to, for example "`swing-07`, scrub to 12:07 in `IMG_4601`".

### Retention at home

- The original is deleted once its session is published, as today.
- Failed videos are kept 3 days so they can be re-sent, as today.
- Clips are cut in scratch for ball tracking and discarded with the scratch directory.

### Migration of the Drive sessions

A one-time command:

1. Refuses to run if anything is in the Drive `inbox/`, `processing/`, or `failed/`, and lists what it found, so the owner decides.
2. Copies each of the five Drive sessions to `golf-sessions/sessions/<session-id>/` with everything except `clip.mp4` and `session.zip`, in their existing layout (older sessions have `01-address.jpg` to `04-finish.jpg` and no `pose.csv`).
3. Verifies every copied file is in the pushed commit with the same size.
4. Only then deletes those session folders from Drive permanently. The empty Drive folder layout stays so `drive` mode still works.
5. Supports a dry run that prints what it would copy and delete.

### Skill

`claude/skills/golf-swing-analysis/SKILL.md` gains a GitHub source as the primary path: the owner adds `golf-sessions` with **+ > Add from GitHub**, the skill lists `sessions/`, takes the newest name (they sort by capture time), and reads the full-size images. It handles both the current layout and the older migrated layout. The Drive path stays for `drive` mode. The skill gives scrub times as above.

## Calls made on the owner's behalf

These were not discussed in detail; the owner can override any of them.

- **Image size.** Full-size images keep today's settings (2560 long edge, quality 90), because at sizes that would cap a session at about 30MB the launch monitor numbers on `07-after-shot.jpg` stop being reliably readable. A 1080p range session is about 0.93MB per swing on GitHub, a detailed simulator session about 2.4MB per swing (about 72MB for 30 swings), so GitHub's 10GB recommendation holds roughly 130 of the largest sessions. The `-small.jpg` copies are not needed in `github` mode.
- **GitHub credential.** A deploy key with write access to `golf-sessions` only, stored as a SOPS secret in the homelab. Deploy keys do not expire.
- **Tailscale.** The operator authenticates with a Tailscale OAuth client, which does not expire like a node key. The tailnet needs MagicDNS and HTTPS certificates turned on.
- **Repo name.** `golf-sessions`, separate from `weekly-notes`.
- **Migration layout.** Old sessions keep their old file set rather than being regenerated (the originals are gone).
- **Partial uploads.** Uploads that never finish are deleted after 1 day.
- **Resuming after Safari reloads the page.** The owner picks the same video again and the upload continues from where it stopped (matched by name, size, and modified time). The page does not copy videos into browser storage.
- **Seeing a failure.** The upload page lists recent uploads with their status: uploading, waiting, processing, published (with the session id), or failed (with the reason from the error file). Nothing about failures is pushed to `golf-sessions`.
- **Polling.** The cronjob keeps its 3 minute schedule and `Forbid` concurrency, now polling the homelab volume.

## Constraints

- Two repos. golf-etl changes go through normal PRs here. Homelab changes (Tailscale operator, upload service, volume, cronjob changes, secrets) go through the homelab repo's OpenSpec workflow (`openspec/AGENTS.md`): a change proposal with delta specs, validated with `openspec validate --strict`, approved before implementation. The existing `openspec/specs/golf-etl/spec.md` is the spec being modified.
- The upload service and the cronjob share a `local-path` ReadWriteOnce volume, so both are pinned to the worker `talos-hk3-kam`. The control plane node has about 3GB free and cannot hold videos.
- A run must not download the whole `golf-sessions` history to publish one session; the repo will grow to GBs.
- Commits, PR descriptions, and docs follow the owner's conventions: subject-only semantic commit messages with `git commit -s`, short lowercase PR bodies, no em or en dashes, docs describe what is true now.
- `docs/design.md` and `README.md` are rewritten to describe the new flow as current truth, with Drive as the alternative mode.
- The existing behaviors in `docs/design.md` (claiming, recovery after a killed run, retry counts, `failed/` after 3 attempts, duplicate copies, re-upload of a failed video, atomic replace of a session) keep working in both modes.

## Owner's manual steps

The builder should list these in the plan and wait on them where needed:

- Install the Tailscale app on the iPhone and sign in.
- Create the Tailscale OAuth client for the operator and add the operator tags to the tailnet policy. Turn on MagicDNS and HTTPS.
- Add the deploy key to `golf-sessions`.
- Grant the claude.ai GitHub integration access to `golf-sessions`.
- Re-upload the updated skill to claude.ai.

## Left to the builder

- The upload server and page (for example tusd with a small static page), and how a finished upload moves into the inbox.
- How the inbox seam is implemented for a local folder (a native implementation, or `RcloneDrive` pointed at a local path, which `GOLF_REMOTE` already allows).
- How publishing to GitHub works (git with a shallow or sparse checkout, or the Git Data API) and the commit identity.
- How to run the early iPhone check that the upload page delivers the original file (for example a throwaway page on the home network, comparing the uploaded file's checksum and metadata with the original).
- Test strategy, following the existing fakes.
- Removing the `spike/golf-connector-test` branch from `weekly-notes` once the design is built.
