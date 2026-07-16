import json
import zipfile

from azdo_backup_frontend.scanner import scan


def make_dir_run(root, name, projects=("Alpha", "Beta"), errors=0, with_summary=True):
    run = root / name
    (run / "projects").mkdir(parents=True)
    (run / "org.json").write_text(json.dumps({"url": "https://dev.azure.com/contoso"}))
    if with_summary:
        summary = {
            "started_at": "2026-07-10T02:00:00Z",
            "finished_at": "2026-07-10T02:14:30Z",
            "errors": list(range(errors)),
        }
        (run / "summary.json").write_text(json.dumps(summary))
    for project in projects:
        pdir = run / "projects" / project
        pdir.mkdir()
        (pdir / "project.json").write_text("{}")
        (pdir / "work_items.json").write_text("x" * 1000)
    return run


def test_scan_dir_run(tmp_path):
    make_dir_run(tmp_path, "2026-07-10_0200_all")
    runs = scan(tmp_path)
    assert len(runs) == 1
    run = runs[0]
    assert run.id == "2026-07-10_0200_all"
    assert run.kind == "dir"
    assert run.status == "ok"
    assert run.org == "contoso"
    assert run.projects == ["Alpha", "Beta"]
    assert run.created_at.startswith("2026-07-10T02:14:30")
    assert run.duration_seconds == 870.0
    assert run.size_bytes > 2000


def test_scan_errors_and_incomplete(tmp_path):
    make_dir_run(tmp_path, "run_errors", errors=3)
    make_dir_run(tmp_path, "run_incomplete", with_summary=False)
    by_id = {r.id: r for r in scan(tmp_path)}
    assert by_id["run_errors"].status == "completed_with_errors"
    assert by_id["run_errors"].error_count == 3
    assert by_id["run_incomplete"].status == "incomplete"


def test_scan_ignores_unrelated_dirs(tmp_path):
    (tmp_path / "random-folder").mkdir()
    (tmp_path / "notes.txt").write_text("hi")
    assert scan(tmp_path) == []


def test_scan_zip_run(tmp_path):
    zpath = tmp_path / "backup-2026-07-01.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("summary.json", json.dumps({"finished_at": "2026-07-01T03:00:00Z", "errors": []}))
        zf.writestr("org.json", json.dumps({"name": "contoso"}))
        zf.writestr("projects/Gamma/project.json", "{}")
        zf.writestr("projects/Delta/project.json", "{}")
    runs = scan(tmp_path)
    assert len(runs) == 1
    run = runs[0]
    assert run.kind == "zip"
    assert run.status == "ok"
    assert run.org == "contoso"
    assert run.projects == ["Delta", "Gamma"]
    assert run.size_bytes == zpath.stat().st_size


def test_finished_run_size_is_cached_until_summary_changes(tmp_path):
    run = make_dir_run(tmp_path, "cached_run")
    size1 = scan(tmp_path)[0].size_bytes

    # Finished runs are treated as immutable: adding a file without touching
    # summary.json is served from the cache.
    (run / "projects" / "Alpha" / "extra.bin").write_bytes(b"x" * 5000)
    assert scan(tmp_path)[0].size_bytes == size1

    # Rewriting summary.json invalidates the cached size.
    summary = json.loads((run / "summary.json").read_text())
    (run / "summary.json").write_text(json.dumps(summary, indent=2))
    assert scan(tmp_path)[0].size_bytes >= size1 + 5000


def make_sibling_zip(root, name):
    zpath = root / (name + ".zip")
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("summary.json", json.dumps({"errors": []}))
        zf.writestr("projects/Alpha/blob.bin", "x" * 4000)
    return zpath


def test_archive_zip_folds_into_dir_run(tmp_path):
    make_dir_run(tmp_path, "2026-07-16_0300_all")
    zpath = make_sibling_zip(tmp_path, "2026-07-16_0300_all")
    runs = scan(tmp_path)
    assert len(runs) == 1  # one backup -> one history entry, not two
    run = runs[0]
    assert run.kind == "dir"
    assert run.archive_path == str(zpath)
    assert run.archive_size_bytes == zpath.stat().st_size
    assert run.size_bytes > zpath.stat().st_size  # dir contents + archive


def test_standalone_zip_keeps_own_entry(tmp_path):
    make_dir_run(tmp_path, "some_run")
    make_sibling_zip(tmp_path, "unrelated_backup")
    runs = scan(tmp_path)
    assert len(runs) == 2
    kinds = {r.id: r.kind for r in runs}
    assert kinds["unrelated_backup.zip"] == "zip"
    assert all(r.archive_path is None for r in runs)


def test_runs_sorted_newest_first(tmp_path):
    old = make_dir_run(tmp_path, "old_run")
    (old / "summary.json").write_text(json.dumps({"finished_at": "2026-01-01T00:00:00Z"}))
    new = make_dir_run(tmp_path, "new_run")
    (new / "summary.json").write_text(json.dumps({"finished_at": "2026-07-01T00:00:00Z"}))
    assert [r.id for r in scan(tmp_path)] == ["new_run", "old_run"]
