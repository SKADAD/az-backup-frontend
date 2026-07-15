"""Discover backup runs on disk and extract dashboard metadata.

A "run" is either a directory directly under the backups root that looks like
azdo-az-backup output (contains summary.json / org.json / projects/), or a
.zip archive produced with --archive.

The summary.json schema is treated as opaque: known-useful fields are probed
defensively so the dashboard degrades gracefully (falling back to filesystem
metadata) rather than breaking if the backup tool's format evolves.
"""

import json
import os
import threading
import time
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

MARKER_FILES = ("summary.json", "org.json", "manifest.json")

_TIMESTAMP_KEYS = ("finished_at", "completed_at", "ended_at", "started_at", "start_time", "timestamp", "created_at", "date")
_DURATION_KEYS = ("duration_seconds", "duration", "elapsed_seconds", "elapsed")
_ORG_KEYS = ("organization", "org", "org_url", "organization_url", "url", "name")


@dataclass
class BackupRun:
    id: str
    path: str
    kind: str  # "dir" | "zip"
    created_at: Optional[str]  # ISO 8601
    size_bytes: int
    org: Optional[str]
    projects: List[str] = field(default_factory=list)
    status: str = "unknown"  # ok | completed_with_errors | incomplete | running | unknown
    error_count: int = 0
    duration_seconds: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_timestamp(value) -> Optional[str]:
    if isinstance(value, (int, float)) and value > 1_000_000_000:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return None
    return None


def _probe_summary(summary: dict) -> dict:
    """Pull whatever recognizable fields exist out of a summary.json blob."""
    out = {"created_at": None, "duration_seconds": None, "error_count": 0, "projects": []}
    if not isinstance(summary, dict):
        return out

    for key in _TIMESTAMP_KEYS:
        ts = _parse_timestamp(summary.get(key))
        if ts:
            out["created_at"] = ts
            break

    for key in _DURATION_KEYS:
        val = summary.get(key)
        if isinstance(val, (int, float)):
            out["duration_seconds"] = float(val)
            break
    if out["duration_seconds"] is None:
        start = _parse_timestamp(summary.get("started_at") or summary.get("start_time"))
        end = _parse_timestamp(summary.get("finished_at") or summary.get("completed_at"))
        if start and end:
            delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
            out["duration_seconds"] = max(delta.total_seconds(), 0.0)

    errors = summary.get("errors")
    if isinstance(errors, list):
        out["error_count"] = len(errors)
    elif isinstance(errors, int):
        out["error_count"] = errors
    elif isinstance(summary.get("error_count"), int):
        out["error_count"] = summary["error_count"]

    projects = summary.get("projects")
    if isinstance(projects, list):
        for p in projects:
            if isinstance(p, str):
                out["projects"].append(p)
            elif isinstance(p, dict):
                name = p.get("name") or p.get("project")
                if isinstance(name, str):
                    out["projects"].append(name)
    return out


def _probe_org(org: dict) -> Optional[str]:
    if not isinstance(org, dict):
        return None
    for key in _ORG_KEYS:
        val = org.get(key)
        if isinstance(val, str) and val:
            return val.rstrip("/").rsplit("/", 1)[-1] if "://" in val else val
    return None


def _load_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


# Finished runs are immutable, so their tree walk result is cached against
# summary.json's identity; unfinished runs (no summary yet) are always re-walked.
_size_cache: dict = {}


def _cached_dir_size(path: Path) -> int:
    try:
        st = (path / "summary.json").stat()
        signature = (st.st_mtime_ns, st.st_size)
    except OSError:
        return _dir_size(path)
    cached = _size_cache.get(str(path))
    if cached is not None and cached[0] == signature:
        return cached[1]
    size = _dir_size(path)
    _size_cache[str(path)] = (signature, size)
    return size


def _scan_dir_run(path: Path) -> BackupRun:
    summary = _load_json(path / "summary.json")
    probed = _probe_summary(summary or {})
    org = _probe_org(_load_json(path / "org.json") or {})

    projects = probed["projects"]
    projects_dir = path / "projects"
    if not projects and projects_dir.is_dir():
        try:
            projects = sorted(p.name for p in projects_dir.iterdir() if p.is_dir())
        except OSError:
            projects = []

    created_at = probed["created_at"]
    if not created_at:
        try:
            created_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            created_at = None

    if summary is None:
        status = "incomplete"
    elif probed["error_count"] > 0:
        status = "completed_with_errors"
    else:
        status = "ok"

    return BackupRun(
        id=path.name,
        path=str(path),
        kind="dir",
        created_at=created_at,
        size_bytes=_cached_dir_size(path),
        org=org,
        projects=projects,
        status=status,
        error_count=probed["error_count"],
        duration_seconds=probed["duration_seconds"],
    )


def _zip_member_json(zf: zipfile.ZipFile, basename: str) -> Optional[dict]:
    """Read the shallowest member named `basename` from the archive."""
    candidates = [n for n in zf.namelist() if n.split("/")[-1] == basename]
    if not candidates:
        return None
    candidates.sort(key=lambda n: n.count("/"))
    try:
        return json.loads(zf.read(candidates[0]).decode("utf-8"))
    except (KeyError, ValueError, OSError):
        return None


def _scan_zip_run(path: Path) -> BackupRun:
    summary = None
    org = None
    projects: List[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            summary = _zip_member_json(zf, "summary.json")
            org = _probe_org(_zip_member_json(zf, "org.json") or {})
            seen = set()
            for name in zf.namelist():
                parts = name.split("/")
                if "projects" in parts:
                    idx = parts.index("projects")
                    if len(parts) > idx + 1 and parts[idx + 1]:
                        seen.add(parts[idx + 1])
            projects = sorted(seen)
    except (zipfile.BadZipFile, OSError):
        pass

    probed = _probe_summary(summary or {})
    if probed["projects"]:
        projects = probed["projects"]

    created_at = probed["created_at"]
    stat_size = 0
    try:
        st = path.stat()
        stat_size = st.st_size
        if not created_at:
            created_at = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        pass

    if summary is None:
        status = "unknown"
    elif probed["error_count"] > 0:
        status = "completed_with_errors"
    else:
        status = "ok"

    return BackupRun(
        id=path.name,
        path=str(path),
        kind="zip",
        created_at=created_at,
        size_bytes=stat_size,
        org=org,
        projects=projects,
        status=status,
        error_count=probed["error_count"],
        duration_seconds=probed["duration_seconds"],
    )


def _looks_like_run(path: Path) -> bool:
    if any((path / marker).is_file() for marker in MARKER_FILES):
        return True
    if (path / "projects").is_dir():
        return True
    # A run started by this frontend has its log there before the tool writes anything.
    return (path / "frontend.log").is_file()


def scan(root: Path) -> List[BackupRun]:
    runs: List[BackupRun] = []
    if not root.is_dir():
        return runs
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return runs
    for entry in entries:
        if entry.is_dir() and _looks_like_run(entry):
            runs.append(_scan_dir_run(entry))
        elif entry.is_file() and entry.suffix.lower() == ".zip":
            runs.append(_scan_zip_run(entry))
    runs.sort(key=lambda r: r.created_at or "", reverse=True)
    return runs


class ScanCache:
    """TTL cache so the dashboard doesn't re-walk large backup trees on every poll."""

    def __init__(self, ttl_seconds: float = 30.0):
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self._result: Optional[List[BackupRun]] = None
        self._root: Optional[Path] = None
        self._at = 0.0

    def get(self, root: Path, force: bool = False) -> List[BackupRun]:
        with self._lock:
            fresh = (
                not force
                and self._result is not None
                and self._root == root
                and (time.monotonic() - self._at) < self.ttl
            )
            if fresh:
                return self._result
        result = scan(root)
        with self._lock:
            self._result = result
            self._root = root
            self._at = time.monotonic()
        return result

    def invalidate(self) -> None:
        with self._lock:
            self._result = None
