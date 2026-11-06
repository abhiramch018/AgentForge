#!/usr/bin/env python3
"""Restore AgentForge SQLite data from a backup created by backup_sqlite.py.

WARNING: Stop API and worker before restore. Restoring over a live volume with
writers attached can corrupt state.

    docker compose stop api worker web
    docker compose run --rm -v "${PWD}:/src" -v "${PWD}/backups/snap1:/backup" \\
      -w /src api python deploy/restore_sqlite.py --backup-dir /backup --data-dir /data
    docker compose start api worker web
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

DB_FILES = (
    "jobs.db",
    "huey_queue.db",
    "memory.db",
    "approvals.db",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore AgentForge SQLite backup")
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required confirmation flag (destructive to --data-dir contents).",
    )
    args = parser.parse_args()
    if not args.yes:
        print("Refusing to restore without --yes", file=sys.stderr)
        return 2
    backup = args.backup_dir
    data = args.data_dir
    if not backup.is_dir():
        print(f"backup dir missing: {backup}", file=sys.stderr)
        return 1
    data.mkdir(parents=True, exist_ok=True)
    for name in DB_FILES:
        src = backup / name
        if not src.exists():
            print(f"skip (missing in backup): {name}", file=sys.stderr)
            continue
        dest = data / name
        # Remove WAL/SHM so restored main DB is authoritative.
        for suffix in ("", "-wal", "-shm"):
            side = Path(str(dest) + suffix) if suffix else dest
            if side.exists():
                side.unlink()
        shutil.copy2(src, dest)
        print(f"restored {name}")
    art_src = backup / "artifacts"
    if art_src.exists():
        art_dest = data / "artifacts"
        if art_dest.exists():
            shutil.rmtree(art_dest)
        shutil.copytree(art_src, art_dest)
        print("restored artifacts/")
    print(f"restore complete → {data.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
