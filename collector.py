#!/usr/bin/env python3

import re
import os
import socket
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HOST = os.getenv("OGN_DECODER_HOST", "127.0.0.1")
PORT = int(os.getenv("OGN_DECODER_PORT", "50001"))
DATABASE = Path(os.getenv("OGN_DATABASE", BASE_DIR / "database" / "ogn.sqlite3"))

PACKET_RE = re.compile(
    r"^APRS <- (?P<sender>[A-Z0-9]+)>(?P<destination>[A-Z0-9]+)"
)


def open_database() -> sqlite3.Connection:
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS packets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            sender TEXT NOT NULL,
            destination TEXT NOT NULL,
            raw_packet TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            packet_id INTEGER NOT NULL,
            received_at TEXT NOT NULL,
            aircraft_id TEXT NOT NULL,
            destination TEXT NOT NULL,
            protocol TEXT,
            latitude REAL,
            longitude REAL,
            altitude_m REAL,
            course_deg REAL,
            speed_kmh REAL,
            climb_ms REAL,
            turn_rate_degs REAL,
            snr_db REAL,
            frequency_offset_khz REAL,
            FOREIGN KEY (packet_id) REFERENCES packets(id)
        )
        """
    )

    for statement in (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_packet_id ON positions(packet_id)",
        "CREATE INDEX IF NOT EXISTS idx_positions_received_at ON positions(received_at)",
        "CREATE INDEX IF NOT EXISTS idx_positions_aircraft_id ON positions(aircraft_id)",
        "CREATE INDEX IF NOT EXISTS idx_positions_coordinates ON positions(latitude, longitude)",
    ):
        connection.execute(statement)

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_packets_received_at
        ON packets(received_at)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_packets_sender
        ON packets(sender)
        """
    )

    connection.commit()
    return connection


def collect() -> None:
    database = open_database()

    while True:
        try:
            with socket.create_connection((HOST, PORT), timeout=10) as sock:
                sock.settimeout(None)

                with sock.makefile(
                    "r",
                    encoding="utf-8",
                    errors="replace",
                ) as stream:
                    for line in stream:
                        line = line.strip()
                        match = PACKET_RE.match(line)

                        if match is None:
                            continue

                        sender = match.group("sender")
                        destination = match.group("destination")

                        # Ignore the receiver's periodic station beacon.
                        if destination == "OGNSDR":
                            continue

                        received_at = datetime.now(timezone.utc).isoformat()

                        database.execute(
                            """
                            INSERT INTO packets (
                                received_at,
                                sender,
                                destination,
                                raw_packet
                            )
                            VALUES (?, ?, ?, ?)
                            """,
                            (
                                received_at,
                                sender,
                                destination,
                                line,
                            ),
                        )
                        database.commit()

        except (ConnectionError, OSError) as error:
            print(
                f"Decoder connection unavailable: {error}; "
                "retrying in 5 seconds.",
                flush=True,
            )
            time.sleep(5)

        except sqlite3.Error as error:
            print(f"SQLite error: {error}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    collect()
