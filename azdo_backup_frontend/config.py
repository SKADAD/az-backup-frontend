"""Runtime configuration, read from environment variables on each call so the
service picks up changes without restarts and tests can monkeypatch cleanly."""

import os
import shutil
from pathlib import Path

PAT_ENV_VARS = ("AZURE_DEVOPS_EXT_PAT", "AZDO_PAT")


def backup_root() -> Path:
    return Path(os.environ.get("AZDO_BACKUP_ROOT", "~/azdo-backups")).expanduser()


def backup_cmd() -> str:
    return os.environ.get("AZDO_BACKUP_CMD", "azdo-backup")


def default_org() -> str:
    return os.environ.get("AZDO_ORG_URL", "")


def pat_present() -> bool:
    return any(os.environ.get(v) for v in PAT_ENV_VARS)


def tool_available() -> bool:
    return shutil.which(backup_cmd()) is not None
