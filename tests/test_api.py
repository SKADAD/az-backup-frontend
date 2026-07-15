import json
import os
import stat
import time

import pytest
from fastapi.testclient import TestClient

from azdo_backup_frontend import app as app_module


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AZDO_BACKUP_ROOT", str(tmp_path / "backups"))
    app_module.cache.invalidate()
    return TestClient(app_module.app)


def make_stub_tool(tmp_path, monkeypatch, script_body):
    """Install a fake azdo-backup on PATH that runs `script_body` as bash."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "azdo-backup-stub"
    stub.write_text("#!/usr/bin/env bash\n" + script_body)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("AZDO_BACKUP_CMD", str(stub))
    monkeypatch.setenv("AZDO_PAT", "fake-pat-for-tests")


def test_status(client):
    data = client.get("/api/status").json()
    assert data["backup_cmd"]
    assert "pat_present" in data


def test_backups_empty(client):
    data = client.get("/api/backups").json()
    assert data["runs"] == []
    assert data["totals"]["count"] == 0


def test_start_backup_requires_scope(client, tmp_path, monkeypatch):
    make_stub_tool(tmp_path, monkeypatch, "exit 0\n")
    res = client.post("/api/backups", json={"org": "https://dev.azure.com/x"})
    assert res.status_code == 422


def test_start_backup_runs_stub(client, tmp_path, monkeypatch):
    # Stub emits a summary.json into the --output dir like the real tool.
    make_stub_tool(
        tmp_path,
        monkeypatch,
        """
out=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--output" ]]; then out="$2"; shift; fi
  shift
done
echo "backing up to $out"
mkdir -p "$out"
echo '{"finished_at": "2026-07-15T00:00:00Z", "errors": []}' > "$out/summary.json"
echo done
exit 0
""",
    )
    res = client.post(
        "/api/backups",
        json={"org": "https://dev.azure.com/contoso", "all_projects": True},
    )
    assert res.status_code == 202, res.text
    job_id = res.json()["job_id"]

    deadline = time.time() + 10
    while time.time() < deadline:
        snap = client.get(f"/api/jobs/{job_id}").json()
        if snap["state"] != "running":
            break
        time.sleep(0.1)
    assert snap["state"] == "succeeded", snap
    assert any("backing up to" in line for line in snap["lines"])

    runs = client.get("/api/backups?refresh=1").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"
    # frontend.log is written alongside the backup output
    assert os.path.exists(os.path.join(runs[0]["path"], "frontend.log"))


def test_backup_conflict_without_tool(client, monkeypatch):
    monkeypatch.setenv("AZDO_BACKUP_CMD", "definitely-not-a-real-command")
    monkeypatch.setenv("AZDO_PAT", "x")
    res = client.post(
        "/api/backups",
        json={"org": "https://dev.azure.com/x", "all_projects": True},
    )
    assert res.status_code == 409


def test_verify_unknown_run(client):
    res = client.post("/api/backups/nope/verify")
    assert res.status_code == 404


def test_detail_includes_summary(client, tmp_path, monkeypatch):
    root = tmp_path / "backups"
    run = root / "manual_run"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps({"finished_at": "2026-07-01T00:00:00Z"}))
    app_module.cache.invalidate()
    detail = client.get("/api/backups/manual_run").json()
    assert detail["summary"]["finished_at"] == "2026-07-01T00:00:00Z"
