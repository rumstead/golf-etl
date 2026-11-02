import argparse
import functools
import hashlib
import logging
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from golf_etl.config import Settings

log = logging.getLogger("golf_etl")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="golf-etl")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("process", help="process a local video without Drive")
    p.add_argument("video", type=Path)
    p.add_argument("--out", type=Path, default=Path("out"))
    e = sub.add_parser("eval", help="score detection against labeled impact times")
    e.add_argument("labels", type=Path)
    sub.add_parser("poll", aliases=["poll-drive"], help="process everything in the inbox once")
    m = sub.add_parser("migrate-drive", help="move the Drive sessions to the GitHub repo")
    m.add_argument("--dry-run", action="store_true", help="print what it would copy and delete")
    sub.add_parser("upload-server", help="serve the upload page, its status, and tusd's hooks")
    g = sub.add_parser("sim-glyphs", help="build sim readout glyphs from labeled videos")
    g.add_argument("labels", type=Path)
    g.add_argument("out", type=Path)
    s = sub.add_parser("eval-sim", help="score sim readout reads against labeled screen text")
    s.add_argument("labels", type=Path)
    s.add_argument(
        "--glyphs",
        type=Path,
        help="glyphs to read every video with; by default each video is read with glyphs "
        "built from the other videos",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Settings.from_env()
    commands = {
        "process": cmd_process,
        "eval": cmd_eval,
        "poll": cmd_poll,
        "poll-drive": cmd_poll,
        "migrate-drive": cmd_migrate,
        "upload-server": cmd_upload_server,
        "sim-glyphs": cmd_sim_glyphs,
        "eval-sim": cmd_eval_sim,
    }
    return commands[args.command](args, cfg)


def cmd_process(args, cfg: Settings) -> int:
    from golf_etl.pipeline import process_video

    with args.video.open("rb") as fh:
        checksum = hashlib.file_digest(fh, "sha256").hexdigest()
    mtime = datetime.fromtimestamp(args.video.stat().st_mtime, UTC)
    result = process_video(
        args.video, args.out, cfg, source_name=args.video.name, checksum=checksum, uploaded_at=mtime
    )
    print(f"{result.session_dir}: {result.swings} swings, {len(result.rejected)} rejected")
    return 0


def cmd_eval(args, cfg: Settings) -> int:
    from golf_etl.eval import detected_impacts, run

    _, lines = run(args.labels, cfg, detected_impacts)
    print("\n".join(lines))
    return 0


def cmd_sim_glyphs(args, cfg: Settings) -> int:
    from golf_etl.sim import build_glyphs
    from golf_etl.sim_eval import labeled_cells

    glyphs = build_glyphs((cell, text) for _, cell, _, text in labeled_cells(args.labels))
    glyphs.save(args.out)
    classes = Counter(glyphs.labels.tolist())
    print(
        f"{args.out}: {len(glyphs.labels)} glyphs, "
        + " ".join(f"{c} {n}" for c, n in sorted(classes.items()))
    )
    return 0


def cmd_eval_sim(args, cfg: Settings) -> int:
    from golf_etl.sim import Glyphs
    from golf_etl.sim_eval import held_out, score

    if args.glyphs:
        glyphs = Glyphs.load(args.glyphs)
        _, _, _, lines = score(args.labels, lambda video: glyphs)
    else:
        _, _, _, lines = score(args.labels, held_out(args.labels))
    print("\n".join(lines))
    return 0


def missing_drive_token(cfg: Settings) -> bool:
    if cfg.remote.startswith("gdrive:") and not os.environ.get("RCLONE_CONFIG_GDRIVE_TOKEN"):
        print("missing environment variable RCLONE_CONFIG_GDRIVE_TOKEN", file=sys.stderr)
        return True
    return False


def missing_deploy_key(cfg: Settings) -> bool:
    """Every remote but a local file:// repo needs the key, so host key pinning always applies."""
    if not cfg.sessions_repo.startswith("file://") and not Path(cfg.ssh_key_path).is_file():
        print(f"missing deploy key at {cfg.ssh_key_path}", file=sys.stderr)
        return True
    return False


def github_publisher(cfg: Settings):
    from golf_etl.storage.github import GitHubPublisher

    return GitHubPublisher(
        cfg.sessions_repo, Path(cfg.scratch_dir) / "golf-sessions", cfg.ssh_key_path
    )


def cmd_poll(args, cfg: Settings) -> int:
    from golf_etl.pipeline import process_video
    from golf_etl.storage.poller import Poller
    from golf_etl.storage.publish import DrivePublisher
    from golf_etl.storage.rclone import RcloneStore
    from golf_etl.storage.retention import sweep_failed

    if cfg.storage == "github":
        if missing_deploy_key(cfg):
            return 2
        store = RcloneStore(cfg.inbox_root)
        publisher = github_publisher(cfg)
    elif cfg.storage == "drive":
        if missing_drive_token(cfg):
            return 2
        store = RcloneStore(cfg.remote)
        publisher = DrivePublisher(store, cfg)
    else:
        print("GOLF_STORAGE must be github or drive", file=sys.stderr)
        return 2
    version = os.environ.get("GOLF_ETL_VERSION", "dev")
    process = functools.partial(process_video, cfg=cfg, version=version)
    report = Poller(store, publisher, cfg, process, Path(cfg.scratch_dir), version).run()
    log.info("poll: %s", report)
    now = datetime.now(UTC)
    failed = sweep_failed(store, cfg, now)
    swept = publisher.sweep(now)
    if cfg.storage == "github":
        log.info("sweep: deleted %d failed originals", len(failed.deleted))
    else:
        log.info(
            "sweep: deleted %d failed originals and %d session files, %d bytes in sessions",
            len(failed.deleted),
            len(swept.deleted),
            swept.bytes_after,
        )
    return 0


def cmd_migrate(args, cfg: Settings) -> int:
    from golf_etl.storage.migrate import migrate
    from golf_etl.storage.rclone import RcloneStore

    if missing_drive_token(cfg) or missing_deploy_key(cfg):
        return 2
    store = RcloneStore(cfg.remote)
    scratch = Path(cfg.scratch_dir) / "migrate"
    report = migrate(store, github_publisher(cfg), scratch, args.dry_run)
    if report.blocked:
        for path in report.blocked:
            print(path)
        print(f"refused: Drive has {len(report.blocked)} entries in inbox, processing or failed")
        return 1
    for sid in report.copied:
        if args.dry_run:
            print(f"{sid}: would copy, then delete from Drive")
        elif sid in report.deleted:
            print(f"{sid}: copied and deleted from Drive")
        else:
            print(f"{sid}: copied, kept on Drive because the push did not match")
    for sid in report.kept:
        if sid not in report.copied:
            print(f"{sid}: kept on Drive, it has no marker")
    counts = f"{len(report.copied)} copied, {len(report.deleted)} deleted, {len(report.kept)} kept"
    print(f"dry run: {counts}" if args.dry_run else counts)
    return 1 if report.kept else 0


def cmd_upload_server(args, cfg: Settings) -> int:
    from golf_etl.upload import serve

    serve(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
