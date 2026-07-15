"""Console entry point: azdo-backup-frontend"""

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="azdo-backup-frontend",
        description="Local web dashboard and wrapper for the azdo-az-backup CLI.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address (default 127.0.0.1; the app has no auth, keep it local)",
    )
    parser.add_argument("--port", type=int, default=8321, help="port (default 8321)")
    parser.add_argument(
        "--root",
        help="backups root directory (overrides AZDO_BACKUP_ROOT, default ~/azdo-backups)",
    )
    args = parser.parse_args()

    if args.root:
        os.environ["AZDO_BACKUP_ROOT"] = args.root

    import uvicorn

    from .app import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
