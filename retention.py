#!/usr/bin/env python3
"""Remove OGN Monitor packets and positions older than the configured period."""

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import time

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.getenv("OGN_DATABASE", BASE_DIR / "database" / "ogn.sqlite3"))
RETENTION_DAYS = max(1, int(os.getenv("OGN_RETENTION_DAYS", "365")))
RUN_SECONDS = max(300, int(os.getenv("OGN_RETENTION_RUN_SECONDS", "86400")))


def apply_retention(now=None):
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)
    cutoff_text = cutoff.isoformat()
    with closing(sqlite3.connect(DATABASE, timeout=30)) as database:
        database.execute("PRAGMA busy_timeout=30000")
        positions = database.execute(
            "SELECT COUNT(*) FROM positions WHERE received_at < ?", (cutoff_text,)
        ).fetchone()[0]
        packets = database.execute(
            "SELECT COUNT(*) FROM packets WHERE received_at < ?", (cutoff_text,)
        ).fetchone()[0]
        database.execute("BEGIN IMMEDIATE")
        database.execute("DELETE FROM positions WHERE received_at < ?", (cutoff_text,))
        database.execute("DELETE FROM packets WHERE received_at < ?", (cutoff_text,))
        database.commit()
        integrity = database.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite quick_check failed: {integrity}")
        database.execute("PRAGMA optimize")
        database.execute("PRAGMA wal_checkpoint(PASSIVE)")
    print(
        f"Retention OK: {RETENTION_DAYS} days; cutoff={cutoff_text}; "
        f"removed positions={positions}, packets={packets}",
        flush=True,
    )
    return positions, packets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="run continuously")
    arguments = parser.parse_args()
    while True:
        apply_retention()
        if not arguments.loop:
            return
        time.sleep(RUN_SECONDS)


if __name__ == "__main__":
    main()
