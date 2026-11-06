#!/usr/bin/env python3
"""SQLite-safe backup of AgentForge data files (online backup API).

Usage (host, with volume mounted or local data dir)::

    python deploy/backup_sqlite.py --data-dir /data --out-dir ./backups/2026-01-01

Inside Compose (recommended)::

    docker compose run --rm -v "${PWD}/backups:/backup" api \\
      python /app/deploy/backup_sqlite.py --data-dir /data --out-dir /backup/snap1

Note: this image may not include deploy/ if built from Dockerfile.api which
only copies ``app/``. Prefer mounting this script or running from a bind mount:

    docker compose run --rm -v "${PWD}:/src" -v "${PWD}/backups:/backup" \\
      -w /src api python deploy/backup_sqlite.py --data-dir /data --out-dir /backup/snap1
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_FILES = (
    "jobs.db",
    "huey_queue.db",
    "memory.db",
    "approvals.db",
)


def backup_one(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        print(f"skip (missing): {src}", file=sys.stderr)
        return
    # Also copy WAL/SHM sidecars after checkpoint when present is fragile;
    # the backup API consolidates into a consistent dest file.
    with sqlite3.connect(str(src), timeout=60) as source:
        source.execute("PRAGMA busy_timeout=60000")
        with sqlite3.connect(str(dest)) as target:
            source.backup(target)
    # Sidecar files are not required for the backup copy.
    for suffix in ("-wal", "-shm"):
        side = Path(str(src) + suffix)
        if side.exists():
            print(f"note: left live {side.name} on source (backup is self-contained)")


def copy_artifacts(src_root: Path, dest_root: Path) -> None:
    art = src_root / "artifacts"
    if not art.exists():
        return
    target = dest_root / "artifacts"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(art, target)


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite-safe AgentForge backup")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Destination directory (default: backups/<utc-timestamp>)",
    )
    args = parser.parse_args()
    out = args.out_dir
    if out is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = Path("backups") / stamp
    out.mkdir(parents=True, exist_ok=True)
    data = args.data_dir
    for name in DB_FILES:
        backup_one(data / name, out / name)
    copy_artifacts(data, out)
    meta = out / "BACKUP_META.txt"
    meta.write_text(
        f"created_utc={datetime.now(timezone.utc).isoformat()}\n"
        f"source={data.resolve()}\n"
        "method=sqlite3.Connection.backup\n",
        encoding="utf-8",
    )
    print(f"backup written to {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
