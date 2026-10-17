"""The upload page: tusd takes resumable uploads into upload_dir, this moves finished ones into
the inbox, and serves the page, a status JSON, and tusd's HTTP hooks.

The server listens on 127.0.0.1 only; nginx in the same pod is its only client besides tusd.
"""

import json
import logging
import os
import re
import threading
import time
import unicodedata
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path, PurePosixPath
from stat import S_ISREG

from golf_etl.config import Settings
from golf_etl.storage.store import INBOX, STATE, VIDEO_SUFFIXES

log = logging.getLogger(__name__)

SWEEP_INTERVAL_S = 600
# the hook and the sweep can finish the same upload at once
_finish_lock = threading.Lock()


def safe_name(filename: str) -> str:
    name = re.split(r"[/\\]", filename)[-1]
    name = "".join(c for c in name if unicodedata.category(c) != "Cc").lstrip(".")
    return name or "upload.mov"


def finish_upload(upload: Path, filename: str, inbox: Path) -> Path | None:
    """Move a finished tusd upload into inbox and remove its .info.

    Returns the new path, or None when the upload was already finished and its data is gone.
    """
    with _finish_lock:
        info = upload.with_name(f"{upload.name}.info")
        if not upload.exists():
            info.unlink(missing_ok=True)
            return None
        name = PurePosixPath(safe_name(filename))
        inbox.mkdir(parents=True, exist_ok=True)
        dest = inbox / name.name
        n = 2
        while dest.exists():
            dest = inbox / f"{name.stem}-{n}{name.suffix}"
            n += 1
        os.replace(upload, dest)
        info.unlink(missing_ok=True)
        log.info("upload: moved %s to %s", upload.name, dest)
        return dest


def _uploads(upload_dir: Path):
    """(data file, its stat, info) for each tusd upload, skipping ones whose data is gone."""
    for info_path in sorted(upload_dir.glob("*.info")):
        data = info_path.with_suffix("")
        try:
            info = json.loads(info_path.read_text())
            stat = data.stat()
        except (OSError, ValueError):
            continue
        yield data, stat, info


def _filename(info: dict) -> str:
    return (info.get("MetaData") or {}).get("filename", "")


def sweep_uploads(upload_dir: Path, inbox: Path, now: datetime, ttl: timedelta) -> list[str]:
    """Finish complete uploads whose hook was missed, delete unfinished ones older than ttl."""
    done = []
    for data, stat, info in _uploads(upload_dir):
        if data.with_name(f"{data.name}.lock").exists():
            continue
        name = _filename(info)
        if stat.st_size == info.get("Size"):
            if finish_upload(data, name, inbox):
                done.append(name)
        elif now - datetime.fromtimestamp(stat.st_mtime, UTC) > ttl:
            data.unlink(missing_ok=True)
            data.with_name(f"{data.name}.info").unlink(missing_ok=True)
            log.info("upload: deleted unfinished %s (%s)", data.name, safe_name(name))
            done.append(name)
    return done


def status(inbox_root: Path, upload_dir: Path) -> dict:
    """What the page lists: uploads in progress, videos waiting in the inbox, recent results."""
    uploading = [
        {"name": _filename(info), "offset": stat.st_size, "size": info.get("Size")}
        for _, stat, info in _uploads(upload_dir)
    ]
    inbox = inbox_root / INBOX
    waiting = []
    for p in sorted(inbox.iterdir() if inbox.is_dir() else []):
        if p.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        try:  # the poller can claim it while this lists
            stat = p.stat()
        except FileNotFoundError:
            continue
        if S_ISREG(stat.st_mode):
            waiting.append({"name": p.name, "size": stat.st_size})
    try:
        recent = json.loads((inbox_root / STATE).read_text()).get("recent", [])
    except (OSError, ValueError):
        recent = []
    return {"uploading": uploading, "waiting": waiting, "recent": recent}


def hook_response(payload: dict, cfg: Settings) -> dict:
    """Reply to a tusd HTTP hook: reject non-videos, move finished uploads into the inbox."""
    upload = (payload.get("Event") or {}).get("Upload") or {}
    filename = _filename(upload)
    if payload.get("Type") == "pre-create":
        if PurePosixPath(filename).suffix.lower() not in VIDEO_SUFFIXES:
            return {
                "RejectUpload": True,
                "HTTPResponse": {"StatusCode": 400, "Body": "only videos"},
            }
    elif payload.get("Type") == "post-finish":
        path = (upload.get("Storage") or {}).get("Path")
        if path:
            data = Path(path)
            if data.resolve().parent != Path(cfg.upload_dir).resolve():
                log.warning("upload: ignoring %s outside %s", path, cfg.upload_dir)
            else:
                finish_upload(data, filename, Path(cfg.inbox_root) / INBOX)
    return {}


def make_server(cfg: Settings) -> ThreadingHTTPServer:
    page = files("golf_etl").joinpath("upload_page/index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                self.reply(200, "text/html; charset=utf-8", page)
            elif path == "/status":
                body = status(Path(cfg.inbox_root), Path(cfg.upload_dir))
                self.reply(200, "application/json", json.dumps(body).encode())
            else:
                self.reply(404, "text/plain", b"not found")

        def do_POST(self):
            if self.path != "/hooks":
                self.reply(404, "text/plain", b"not found")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length))
            except ValueError:
                self.reply(400, "text/plain", b"bad json")
                return
            try:
                body = hook_response(payload, cfg)
            except Exception:
                log.exception("upload: %s hook failed", payload.get("Type"))
                self.reply(500, "text/plain", b"hook failed")
                return
            self.reply(200, "application/json", json.dumps(body).encode())

        def reply(self, code: int, content_type: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            # the page polls /status every 10 seconds
            log.debug(format, *args)

    return ThreadingHTTPServer(("127.0.0.1", cfg.upload_port), Handler)


def _sweep_forever(cfg: Settings) -> None:
    ttl = timedelta(hours=cfg.upload_ttl_hours)
    while True:
        try:
            sweep_uploads(
                Path(cfg.upload_dir), Path(cfg.inbox_root) / INBOX, datetime.now(UTC), ttl
            )
        except Exception:
            log.exception("upload: sweep failed")
        time.sleep(SWEEP_INTERVAL_S)


def serve(cfg: Settings) -> None:
    server = make_server(cfg)
    threading.Thread(target=_sweep_forever, args=(cfg,), daemon=True).start()
    log.info("upload: serving on 127.0.0.1:%d", cfg.upload_port)
    server.serve_forever()
