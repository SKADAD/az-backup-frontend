"""In-process job runner: launches the backup CLI as a subprocess and buffers
its output so the browser can poll it. Single-user local tool, so an
in-memory registry is deliberate — jobs don't survive a restart, but the
backup output and frontend.log on disk do."""

import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

MAX_LINES = 50_000
DROP_CHUNK = 5_000

# azdo-az-backup exit codes: 0 success, 1 fatal, 2 usage, 3 per-item errors.
_STATE_BY_CODE = {0: "succeeded", 3: "completed_with_errors"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Job:
    def __init__(self, kind: str, cmd: List[str], meta: Optional[dict] = None):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.cmd = list(cmd)
        self.meta = meta or {}
        self.state = "running"
        self.returncode: Optional[int] = None
        self.created_at = _now()
        self.finished_at: Optional[str] = None
        self._lines: List[str] = []
        self._base = 0  # absolute index of _lines[0], for offset polling after drops
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)
            if len(self._lines) > MAX_LINES:
                del self._lines[:DROP_CHUNK]
                self._base += DROP_CHUNK

    def finish(self, returncode: int) -> None:
        with self._lock:
            self.returncode = returncode
            self.state = _STATE_BY_CODE.get(returncode, "failed")
            self.finished_at = _now()

    def snapshot(self, offset: int = 0) -> dict:
        with self._lock:
            start = max(offset - self._base, 0)
            return {
                "id": self.id,
                "kind": self.kind,
                "state": self.state,
                "returncode": self.returncode,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
                "meta": dict(self.meta),
                "lines": self._lines[start:],
                "next_offset": self._base + len(self._lines),
            }

    def brief(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "state": self.state,
                "returncode": self.returncode,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
                "meta": dict(self.meta),
            }


class JobManager:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(
        self,
        kind: str,
        cmd: List[str],
        log_file: Optional[Path] = None,
        meta: Optional[dict] = None,
        on_finish=None,
    ) -> Job:
        job = Job(kind, cmd, meta)
        with self._lock:
            self._jobs[job.id] = job
        thread = threading.Thread(
            target=self._run, args=(job, cmd, log_file, on_finish), daemon=True
        )
        thread.start()
        return job

    def _run(self, job: Job, cmd: List[str], log_file: Optional[Path], on_finish) -> None:
        log_handle = None

        def emit(line: str) -> None:
            job.append(line)
            if log_handle:
                log_handle.write(line + "\n")
                log_handle.flush()

        try:
            if log_file is not None:
                log_file.parent.mkdir(parents=True, exist_ok=True)
                log_handle = open(log_file, "a", encoding="utf-8")

            emit(f"$ {' '.join(cmd)}")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    bufsize=1,
                    env=os.environ.copy(),
                )
            except FileNotFoundError:
                emit(f"error: command not found: {cmd[0]}")
                job.finish(127)
                return

            assert proc.stdout is not None
            for raw in proc.stdout:
                emit(raw.rstrip("\n"))
            returncode = proc.wait()
            emit(f"[exit code {returncode}]")
            job.finish(returncode)
        except Exception as exc:  # surface unexpected runner errors in the job log
            emit(f"runner error: {exc!r}")
            job.finish(1)
        finally:
            if log_handle:
                log_handle.close()
            if on_finish:
                try:
                    on_finish(job)
                except Exception:
                    pass

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[dict]:
        with self._lock:
            jobs = list(self._jobs.values())
        return sorted((j.brief() for j in jobs), key=lambda j: j["created_at"], reverse=True)

    def running_backup_paths(self) -> Dict[str, dict]:
        """Map of output path -> job brief, for backups still in flight."""
        with self._lock:
            jobs = list(self._jobs.values())
        out = {}
        for job in jobs:
            if job.kind == "backup" and job.state == "running" and job.meta.get("output"):
                out[job.meta["output"]] = job.brief()
        return out
