"""Publisher that commits sessions to a git repo on GitHub.

Each call works in a fresh shallow, blob-filtered clone with only its own session checked out,
so the work dir stays small however many sessions the repo holds.
"""

import json
import os
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from golf_etl.storage.retention import SweepReport
from golf_etl.storage.store import MARKER, SESSIONS

EXCLUDE = ("clip.mp4", "*-small.jpg")  # fnmatch on file names
KNOWN_HOSTS = Path(__file__).with_name("github_known_hosts")
IDENTITY = {
    "GIT_AUTHOR_NAME": "golf-etl",
    "GIT_AUTHOR_EMAIL": "golf-etl@localhost",
    "GIT_COMMITTER_NAME": "golf-etl",
    "GIT_COMMITTER_EMAIL": "golf-etl@localhost",
}


class GitHubPublisher:
    """Sessions in the sessions/ folder of a git remote, one commit per publish."""

    def __init__(self, remote: str, work: Path, ssh_key: str | None = None, branch: str = "main"):
        self.remote = remote
        self.work = work
        self.branch = branch
        self.env = {"GIT_TERMINAL_PROMPT": "0", **IDENTITY}
        if ssh_key:
            ssh = ["ssh", "-i", ssh_key, "-o", "IdentitiesOnly=yes"]
            ssh += ["-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}"]
            self.env["GIT_SSH_COMMAND"] = shlex.join(ssh)

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", "-C", str(self.work), *args],
            capture_output=True,
            text=True,
            errors="replace",
            env={**os.environ, **self.env},
            check=False,
        )
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {proc.stderr.strip()}")
        return proc

    def _checkout(self, session_id: str) -> bool:
        """Clone into an empty work dir with only this session checked out.

        False when the remote has no branch yet; the work dir is then on an unborn branch.
        """
        shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(parents=True)
        self._git("init", "-b", self.branch)
        self._git("remote", "add", "origin", self.remote)
        self._git("sparse-checkout", "set", "--no-cone", f"/{SESSIONS}/{session_id}/")
        fetch = self._git(
            "fetch", "--depth", "1", "--filter=blob:none", "origin", self.branch, check=False
        )
        if fetch.returncode != 0:
            if "couldn't find remote ref" in fetch.stderr:
                return False
            raise RuntimeError(f"git fetch failed: {fetch.stderr.strip()}")
        self._git("checkout", "-B", self.branch, "FETCH_HEAD")
        return True

    def publish(self, session_dir: Path, session_id: str, marker: dict) -> None:
        """Replace sessions/<id>/ in one commit and push it. Nothing changed means no commit."""
        self._checkout(session_id)
        folder = f"{SESSIONS}/{session_id}"
        dest = self.work / folder
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(session_dir, dest, ignore=shutil.ignore_patterns(*EXCLUDE))
        (dest / MARKER).write_text(json.dumps(marker, indent=2))
        self._git("add", "-A", folder)
        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return
        self._git("commit", "-m", f"publish {session_id}")
        self._git("push", "origin", self.branch)

    def published_files(self, session_id: str) -> dict[str, int]:
        """Path under the session folder to size, as the remote branch has it now."""
        if not self._checkout(session_id):
            return {}
        prefix = f"{SESSIONS}/{session_id}/"
        out = self._git("ls-tree", "-r", "-l", "-z", "FETCH_HEAD", prefix).stdout
        files = {}
        for entry in filter(None, out.split("\0")):
            meta, path = entry.split("\t", 1)
            files[path.removeprefix(prefix)] = int(meta.split()[3])
        return files

    def sweep(self, now: datetime) -> SweepReport:
        """GitHub sessions never expire."""
        return SweepReport()
