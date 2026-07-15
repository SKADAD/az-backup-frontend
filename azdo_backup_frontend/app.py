"""FastAPI application: JSON API plus the static dashboard."""

import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, config
from .jobs import JobManager
from .scanner import ScanCache, _load_json

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="AzDO Backup Console", version=__version__)
jobs = JobManager()
cache = ScanCache()


class BackupRequest(BaseModel):
    org: str = Field(min_length=1)
    project: Optional[str] = None
    all_projects: bool = False
    exclude_projects: Optional[str] = None
    workers: Optional[int] = Field(default=None, ge=1, le=64)
    skip_repos: bool = False
    archive: bool = False
    dry_run: bool = False


class OrgRequest(BaseModel):
    org: str = Field(min_length=1)


def _safe_run_name(request: BackupRequest) -> str:
    suffix = "all" if request.all_projects else (request.project or "backup")
    suffix = re.sub(r"[^A-Za-z0-9._-]+", "-", suffix).strip("-") or "backup"
    return time.strftime("%Y-%m-%d_%H%M%S") + "_" + suffix[:60]


def _find_run(run_id: str):
    for run in cache.get(config.backup_root(), force=True):
        if run.id == run_id:
            return run
    return None


@app.get("/api/status")
def status():
    return {
        "version": __version__,
        "backup_root": str(config.backup_root()),
        "backup_cmd": config.backup_cmd(),
        "tool_available": config.tool_available(),
        "pat_present": config.pat_present(),
        "pat_env_vars": list(config.PAT_ENV_VARS),
        "default_org": config.default_org(),
    }


@app.get("/api/backups")
def list_backups(refresh: bool = Query(default=False)):
    root = config.backup_root()
    runs = [run.to_dict() for run in cache.get(root, force=refresh)]

    running = jobs.running_backup_paths()
    known_paths = set()
    for run in runs:
        known_paths.add(run["path"])
        if run["path"] in running:
            run["status"] = "running"
            run["job_id"] = running[run["path"]]["id"]
    # A run whose directory hasn't been created yet still shows up as running.
    for path, brief in running.items():
        if path not in known_paths:
            runs.insert(0, {
                "id": Path(path).name,
                "path": path,
                "kind": "dir",
                "created_at": brief["created_at"],
                "size_bytes": 0,
                "org": brief["meta"].get("org"),
                "projects": [],
                "status": "running",
                "error_count": 0,
                "duration_seconds": None,
                "job_id": brief["id"],
            })

    finished = [r for r in runs if r["status"] != "running"]
    all_projects = sorted({p for r in runs for p in r["projects"]})
    last = finished[0] if finished else None
    return {
        "backup_root": str(root),
        "runs": runs,
        "totals": {
            "count": len(runs),
            "size_bytes": sum(r["size_bytes"] for r in runs),
            "projects": all_projects,
            "last_run": last,
        },
    }


@app.get("/api/backups/{run_id}")
def backup_detail(run_id: str):
    run = _find_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backup not found")
    detail = run.to_dict()
    if run.kind == "dir":
        detail["summary"] = _load_json(Path(run.path) / "summary.json")
        detail["org_info"] = _load_json(Path(run.path) / "org.json")
    return detail


@app.post("/api/backups", status_code=202)
def start_backup(request: BackupRequest):
    if bool(request.project) == request.all_projects:
        raise HTTPException(status_code=422, detail="choose either a project or all projects")
    if not config.tool_available():
        raise HTTPException(
            status_code=409,
            detail=f"backup command '{config.backup_cmd()}' not found on PATH",
        )
    if not config.pat_present():
        raise HTTPException(
            status_code=409,
            detail="no PAT found; set AZURE_DEVOPS_EXT_PAT or AZDO_PAT for this service",
        )

    root = config.backup_root()
    run_name = _safe_run_name(request)
    out_dir = root / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [config.backup_cmd(), "backup", "--org", request.org, "--output", str(out_dir)]
    if request.all_projects:
        cmd.append("--all-projects")
        if request.exclude_projects:
            cmd += ["--exclude-projects", request.exclude_projects]
    else:
        cmd += ["--project", request.project]
    if request.workers:
        cmd += ["--workers", str(request.workers)]
    if request.skip_repos:
        cmd.append("--skip-repos")
    if request.archive:
        cmd.append("--archive")
    if request.dry_run:
        cmd.append("--dry-run")

    job = jobs.start(
        "backup",
        cmd,
        log_file=out_dir / "frontend.log",
        meta={"output": str(out_dir), "run_id": run_name, "org": request.org},
        on_finish=lambda _job: cache.invalidate(),
    )
    cache.invalidate()
    return {"job_id": job.id, "run_id": run_name, "output": str(out_dir)}


@app.post("/api/backups/{run_id}/verify", status_code=202)
def verify_backup(run_id: str):
    run = _find_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backup not found")
    if not config.tool_available():
        raise HTTPException(
            status_code=409,
            detail=f"backup command '{config.backup_cmd()}' not found on PATH",
        )
    cmd = [config.backup_cmd(), "verify", "--source", run.path]
    job = jobs.start("verify", cmd, meta={"run_id": run_id})
    return {"job_id": job.id, "run_id": run_id}


@app.post("/api/org/projects")
def list_org_projects(request: OrgRequest):
    if not config.tool_available():
        raise HTTPException(
            status_code=409,
            detail=f"backup command '{config.backup_cmd()}' not found on PATH",
        )
    try:
        proc = subprocess.run(
            [config.backup_cmd(), "list-projects", "--org", request.org],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="list-projects timed out")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise HTTPException(status_code=502, detail="list-projects failed: " + " / ".join(tail))
    projects = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return {"projects": projects}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": jobs.list()}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str, offset: int = Query(default=0, ge=0)):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.snapshot(offset)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")
