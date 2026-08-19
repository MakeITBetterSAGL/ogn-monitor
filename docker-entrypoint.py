#!/usr/bin/env python3

import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request


BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.getenv("OGN_DATABASE", "/data/ogn.sqlite3"))
DDB_FILE = Path(os.getenv("OGN_DDB_FILE", "/data/ogn-ddb.json"))
DDB_URL = "https://ddb.glidernet.org/download/?j=1"
DDB_REFRESH_SECONDS = int(os.getenv("OGN_DDB_REFRESH_SECONDS", "86400"))
WEB_HOST = os.getenv("OGN_WEB_HOST", "0.0.0.0")
WEB_PORT = os.getenv("OGN_WEB_PORT", "5000")
children = []
stopping = False


def update_ddb() -> None:
    DDB_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with urllib.request.urlopen(DDB_URL, timeout=30) as response:
            payload = response.read()
        document = json.loads(payload)
        if not isinstance(document.get("devices"), list):
            raise ValueError("response does not contain a devices list")
        with tempfile.NamedTemporaryFile(
            dir=DDB_FILE.parent, prefix="ogn-ddb-", suffix=".json", delete=False
        ) as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        temporary_path.replace(DDB_FILE)
        print("Updated the public OGN Devices Database.", flush=True)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"OGN Devices Database update skipped: {error}", flush=True)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def ddb_update_loop() -> None:
    if DDB_REFRESH_SECONDS <= 0:
        return
    while not stopping:
        update_ddb()
        for _ in range(DDB_REFRESH_SECONDS):
            if stopping:
                return
            time.sleep(1)


def start(*command: str) -> subprocess.Popen:
    process = subprocess.Popen(command, cwd=BASE_DIR)
    children.append(process)
    return process


def stop_children(*_args) -> None:
    global stopping
    stopping = True
    for child in children:
        if child.poll() is None:
            child.terminate()


def database_ready() -> bool:
    if not DATABASE.exists():
        return False
    try:
        with sqlite3.connect(DATABASE, timeout=1) as database:
            row = database.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'packets'"
            ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def wait_for_children() -> None:
    for child in children:
        if child.poll() is None:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()


def main() -> int:
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, stop_children)
    signal.signal(signal.SIGINT, stop_children)

    if DDB_REFRESH_SECONDS > 0:
        threading.Thread(target=ddb_update_loop, daemon=True).start()

    start(sys.executable, str(BASE_DIR / "collector.py"))
    deadline = time.monotonic() + 30
    while not database_ready() and time.monotonic() < deadline:
        if children[0].poll() is not None:
            return children[0].returncode or 1
        time.sleep(0.25)
    if not database_ready():
        print("Collector did not initialize the database within 30 seconds.", flush=True)
        stop_children()
        wait_for_children()
        return 1

    start(sys.executable, str(BASE_DIR / "parser.py"))
    start(sys.executable, str(BASE_DIR / "retention.py"), "--loop")
    start(
        sys.executable,
        "-m",
        "gunicorn",
        "--workers",
        "1",
        "--threads",
        "4",
        "--bind",
        f"{WEB_HOST}:{WEB_PORT}",
        "app:app",
    )

    while not stopping:
        for child in children:
            return_code = child.poll()
            if return_code is not None:
                print(
                    f"Application component exited with status {return_code}; stopping.",
                    flush=True,
                )
                stop_children()
                wait_for_children()
                return return_code or 1
        time.sleep(1)

    wait_for_children()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
