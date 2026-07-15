# azdo-backup-frontend

A local web frontend and dashboard for [azdo-az-backup](https://github.com/SKADAD/azdo-az-backup).
It runs on the same machine that performs the backups, wraps the `azdo-backup`
CLI, and gives you:

- **Dashboard** — when each backup ran, size on disk, project names, org,
  duration, and completion status, plus a size-per-run chart.
- **Run backups from the browser** — pick an org and project (or all
  projects), start a backup, and watch the live log.
- **Verify** — run `azdo-backup verify` against any backup and see the result.
- **History from disk** — the dashboard is built by scanning the backups
  folder (`summary.json`, `org.json`, `projects/`, and `.zip` archives), so
  backups made directly with the CLI show up too.

Restores are intentionally not exposed in the UI — use the `azdo-backup restore`
CLI for those.

## Requirements

- Python 3.9+
- [azdo-az-backup](https://github.com/SKADAD/azdo-az-backup) installed so that
  `azdo-backup` is on `PATH` (plus `git`, which it needs for repo mirrors)
- An Azure DevOps PAT exported as `AZURE_DEVOPS_EXT_PAT` or `AZDO_PAT`

## Install & run

```bash
pip install git+https://github.com/SKADAD/azdo-az-backup   # the backup tool
pip install .                                              # this frontend

export AZURE_DEVOPS_EXT_PAT=...   # PAT with work items / code / test / project scopes
azdo-backup-frontend              # serves http://127.0.0.1:8321
```

Open http://127.0.0.1:8321 in a browser on the same machine.

### Options

| Setting | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address. The app has **no authentication** — keep it on localhost. |
| `--port` | `8321` | Port. |
| `--root` / `AZDO_BACKUP_ROOT` | `~/azdo-backups` | Backups root. New runs get timestamped subfolders here; existing content is scanned for the dashboard. |
| `AZDO_BACKUP_CMD` | `azdo-backup` | Path/name of the backup CLI, if it isn't on `PATH`. |
| `AZDO_ORG_URL` | – | Pre-fills the organization URL in the UI. |

## How backups are organized

Each backup started from the UI gets its own folder under the root:

```
~/azdo-backups/
  2026-07-15_030000_all/
    frontend.log          # live log captured by the frontend
    summary.json          # written by azdo-backup
    org.json
    projects/<name>/...
  2026-07-01_020000_MyProject.zip   # --archive runs / archives you made yourself
```

Any directory under the root containing `summary.json` / `org.json` /
`projects/`, and any `.zip` archive, is treated as a backup run. Metadata is
read defensively, so runs still appear (with filesystem dates) even if
`summary.json` is missing or its format changes.

## Security notes

- The PAT is **never** stored, logged, or accepted through the UI. The
  frontend simply inherits `AZURE_DEVOPS_EXT_PAT` / `AZDO_PAT` from its own
  environment and the backup subprocesses pick it up from there.
- The server binds to `127.0.0.1` by default and has no auth; don't expose it
  beyond the backup machine.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

The UI is plain HTML/CSS/JS in `azdo_backup_frontend/static/` — no build step.
