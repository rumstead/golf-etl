import argparse
import functools
import hashlib
import logging
import os
import sys
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
    sub.add_parser("poll-drive", help="process everything in the Drive inbox once")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Settings.from_env()
    return {"process": cmd_process, "eval": cmd_eval, "poll-drive": cmd_poll}[args.command](
        args, cfg
    )


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


def cmd_poll(args, cfg: Settings) -> int:
    from golf_etl.drive.poller import Poller
    from golf_etl.drive.rclone import RcloneDrive
    from golf_etl.drive.retention import sweep
    from golf_etl.pipeline import process_video

    if cfg.remote.startswith("gdrive:") and not os.environ.get("RCLONE_CONFIG_GDRIVE_TOKEN"):
        print("missing environment variable RCLONE_CONFIG_GDRIVE_TOKEN", file=sys.stderr)
        return 2
    version = os.environ.get("GOLF_ETL_VERSION", "dev")
    drive = RcloneDrive(cfg.remote)
    process = functools.partial(process_video, cfg=cfg, version=version)
    report = Poller(drive, cfg, process, Path(cfg.scratch_dir), version).run()
    log.info("poll: %s", report)
    swept = sweep(drive, cfg, datetime.now(UTC))
    log.info("sweep: deleted %d, %d bytes in sessions", len(swept.deleted), swept.bytes_after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
