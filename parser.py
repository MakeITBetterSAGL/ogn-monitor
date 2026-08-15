#!/usr/bin/env python3

import re
import os
import sqlite3
import time
from pathlib import Path

import aprslib

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.getenv("OGN_DATABASE", BASE_DIR / "database" / "ogn.sqlite3"))
POLL_SECONDS = int(os.getenv("OGN_PARSER_POLL_SECONDS", "2"))

COMMENT_RE = re.compile(
    r"(?P<climb>[+-]?\d+)fpm"
    r".*?"
    r"(?P<snr>[+-]?\d+(?:\.\d+)?)dB"
    r"\s+"
    r"(?P<freq>[+-]?\d+(?:\.\d+)?)kHz"
)

PROTOCOLS = {
    "OGFLR": "FLARM",
    "OGNFNT": "FANET",
    "OGADSL": "ADS-L",
}


def initialize_parser_state(db: sqlite3.Connection) -> None:
    """Create a durable cursor so every packet is examined exactly once."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS parser_state (
            name TEXT PRIMARY KEY,
            last_packet_id INTEGER NOT NULL
        )
        """
    )
    db.execute(
        """
        INSERT OR IGNORE INTO parser_state (name, last_packet_id)
        SELECT 'positions', COALESCE(MAX(packet_id), 0)
        FROM positions
        """
    )
    db.commit()


def parse_comment(
    comment: str,
) -> tuple[float | None, float | None, float | None]:
    match = COMMENT_RE.search(comment or "")

    if match is None:
        return None, None, None

    climb_ms = float(match.group("climb")) * 0.00508
    snr_db = float(match.group("snr"))
    frequency_offset_khz = float(match.group("freq"))

    return climb_ms, snr_db, frequency_offset_khz


def process_pending(db: sqlite3.Connection) -> int:
    rows = db.execute(
        """
        SELECT
            id,
            received_at,
            sender,
            destination,
            raw_packet
        FROM packets
        WHERE id > (
            SELECT last_packet_id
            FROM parser_state
            WHERE name = 'positions'
        )
          AND destination != 'OGNSDR'
        ORDER BY id
        LIMIT 500
        """
    ).fetchall()

    inserted = 0

    for packet_id, received_at, sender, destination, raw_packet in rows:
        packet = raw_packet.removeprefix("APRS <- ").strip()

        try:
            parsed = aprslib.parse(packet)
        except Exception as error:
            print(
                f"Packet {packet_id} could not be decoded: {error}",
                flush=True,
            )
            continue

        latitude = parsed.get("latitude")
        longitude = parsed.get("longitude")

        if latitude is None or longitude is None:
            continue

        # aprslib normalizes APRS altitude to metres and speed to km/h.
        # Store those values directly; converting them again would make both
        # measurements incorrect.
        altitude = parsed.get("altitude")
        altitude_m = (
            float(altitude)
            if altitude is not None
            else None
        )

        speed = parsed.get("speed")
        speed_kmh = (
            float(speed)
            if speed is not None
            else None
        )

        course_deg = parsed.get("course")
        comment = parsed.get("comment", "")

        climb_ms, snr_db, frequency_offset_khz = parse_comment(comment)

        try:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO positions (
                    packet_id,
                    received_at,
                    aircraft_id,
                    destination,
                    protocol,
                    latitude,
                    longitude,
                    altitude_m,
                    course_deg,
                    speed_kmh,
                    climb_ms,
                    turn_rate_degs,
                    snr_db,
                    frequency_offset_khz
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    packet_id,
                    received_at,
                    sender,
                    destination,
                    PROTOCOLS.get(destination, destination),
                    float(latitude),
                    float(longitude),
                    altitude_m,
                    float(course_deg) if course_deg is not None else None,
                    speed_kmh,
                    climb_ms,
                    snr_db,
                    frequency_offset_khz,
                ),
            )

            if cursor.rowcount > 0:
                inserted += 1

        except sqlite3.Error as error:
            print(
                f"SQLite error for packet {packet_id}: {error}",
                flush=True,
            )

    if rows:
        db.execute(
            """
            UPDATE parser_state
            SET last_packet_id = ?
            WHERE name = 'positions'
            """,
            (rows[-1][0],),
        )

    db.commit()
    return inserted


def main() -> None:
    with sqlite3.connect(DATABASE) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        initialize_parser_state(db)

        while True:
            inserted = process_pending(db)

            if inserted:
                print(f"Inserted {inserted} positions.", flush=True)

            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
