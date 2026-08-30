#!/usr/bin/env python3

import re
import os
import math
import sqlite3
import time
from pathlib import Path

import aprslib

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.getenv("OGN_DATABASE", BASE_DIR / "database" / "ogn.sqlite3"))
POLL_SECONDS = int(os.getenv("OGN_PARSER_POLL_SECONDS", "2"))


def optional_float(name: str) -> float | None:
    value = os.getenv(name, "").strip()
    return float(value) if value else None


STATION_LATITUDE = optional_float("OGN_STATION_LATITUDE")
STATION_LONGITUDE = optional_float("OGN_STATION_LONGITUDE")
MAX_RADIUS_KM = optional_float("OGN_FILTER_MAX_RADIUS_KM")
MIN_ALTITUDE_M = optional_float("OGN_FILTER_MIN_ALTITUDE_M")
MAX_ALTITUDE_M = optional_float("OGN_FILTER_MAX_ALTITUDE_M")


def validate_filter_configuration() -> None:
    if MAX_RADIUS_KM is not None and MAX_RADIUS_KM <= 0:
        raise ValueError("OGN_FILTER_MAX_RADIUS_KM must be greater than zero")
    if STATION_LATITUDE is not None and not -90 <= STATION_LATITUDE <= 90:
        raise ValueError("OGN_STATION_LATITUDE must be between -90 and 90")
    if STATION_LONGITUDE is not None and not -180 <= STATION_LONGITUDE <= 180:
        raise ValueError("OGN_STATION_LONGITUDE must be between -180 and 180")
    if (
        MIN_ALTITUDE_M is not None and MAX_ALTITUDE_M is not None
        and MIN_ALTITUDE_M > MAX_ALTITUDE_M
    ):
        raise ValueError(
            "OGN_FILTER_MIN_ALTITUDE_M cannot exceed OGN_FILTER_MAX_ALTITUDE_M"
        )


validate_filter_configuration()

CLIMB_RE = re.compile(r"(?<!\w)(?P<value>[+-]?\d+)fpm\b", re.IGNORECASE)
SNR_RE = re.compile(
    r"(?<!\w)(?P<value>[+-]?\d+(?:\.\d+)?)dB(?!m)\b",
    re.IGNORECASE,
)
FREQUENCY_RE = re.compile(
    r"(?<!\w)(?P<value>[+-]?\d+(?:\.\d+)?)kHz\b",
    re.IGNORECASE,
)

PROTOCOLS = {
    "OGFLR": "FLARM",
    "OGNFNT": "FANET",
    "OGADSL": "ADS-L",
    "OGADSB": "ADS-B",
    "ONADSB": "ADS-B",
}


def protocol_name(destination: str, sender: str = "") -> str:
    """Return the traffic source identified by the APRS destination/TOCALL.

    The sender prefix describes the address type, not the receiving protocol.
    In particular, an ICA-prefixed target is not necessarily an ADS-B target.
    """
    normalized = (destination or "").strip().upper()
    if normalized in PROTOCOLS:
        return PROTOCOLS[normalized]
    return destination


def distance_km(latitude_1, longitude_1, latitude_2, longitude_2):
    radius_km = 6371.0088
    lat_1 = math.radians(latitude_1)
    lat_2 = math.radians(latitude_2)
    delta_lat = lat_2 - lat_1
    delta_lon = math.radians(longitude_2 - longitude_1)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_1) * math.cos(lat_2) * math.sin(delta_lon / 2) ** 2
    )
    return radius_km * 2 * math.atan2(
        math.sqrt(haversine), math.sqrt(1 - haversine)
    )


def filter_reason(latitude, longitude, altitude_m):
    if MAX_RADIUS_KM is not None:
        if STATION_LATITUDE is None or STATION_LONGITUDE is None:
            raise RuntimeError(
                "Station coordinates are required when the radius filter is enabled"
            )
        if distance_km(
            STATION_LATITUDE, STATION_LONGITUDE, latitude, longitude
        ) > MAX_RADIUS_KM:
            return "radius"
    # Unknown altitude is retained; altitude values are metres AMSL.
    if altitude_m is not None:
        if MIN_ALTITUDE_M is not None and altitude_m < MIN_ALTITUDE_M:
            return "minimum altitude"
        if MAX_ALTITUDE_M is not None and altitude_m > MAX_ALTITUDE_M:
            return "maximum altitude"
    return None


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


def reclassify_legacy_adsb(db: sqlite3.Connection) -> int:
    """Correct ADS-B labels created by the v1.4.0 sender-prefix heuristic."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS parser_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    migration_name = "protocol_source_tocall_v1"
    if db.execute(
        "SELECT 1 FROM parser_migrations WHERE name = ?",
        (migration_name,),
    ).fetchone():
        return 0

    corrected = 0
    rows = db.execute(
        "SELECT id, destination FROM positions WHERE protocol = 'ADS-B'"
    ).fetchall()
    for position_id, destination in rows:
        protocol = protocol_name(destination)
        if protocol != "ADS-B":
            db.execute(
                "UPDATE positions SET protocol = ? WHERE id = ?",
                (protocol, position_id),
            )
            corrected += 1

    db.execute(
        "INSERT INTO parser_migrations (name) VALUES (?)",
        (migration_name,),
    )
    db.commit()
    return corrected


def parse_comment(
    comment: str,
) -> tuple[float | None, float | None, float | None]:
    """Extract available radio values without requiring a fixed field order.

    OGN comments vary by source. For example, corrected packets may include an
    error-count token between SNR and frequency, while PilotAware comments omit
    climb rate and can append estimated transmitter power in dBm.
    """
    comment = comment or ""
    climb_match = CLIMB_RE.search(comment)
    snr_match = SNR_RE.search(comment)
    frequency_match = FREQUENCY_RE.search(comment)

    climb_ms = (
        float(climb_match.group("value")) * 0.00508
        if climb_match else None
    )
    snr_db = float(snr_match.group("value")) if snr_match else None
    frequency_offset_khz = (
        float(frequency_match.group("value")) if frequency_match else None
    )

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
    filtered_packet_ids = []
    filtered_counts = {}

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
        latitude = float(latitude)
        longitude = float(longitude)
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

        reason = filter_reason(latitude, longitude, altitude_m)
        if reason is not None:
            filtered_packet_ids.append((packet_id,))
            filtered_counts[reason] = filtered_counts.get(reason, 0) + 1
            continue

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
                    protocol_name(destination, sender),
                    latitude,
                    longitude,
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

    if filtered_packet_ids:
        # Remove rejected raw packets too, so enabled filters reduce storage.
        db.executemany("DELETE FROM packets WHERE id = ?", filtered_packet_ids)
        summary = ", ".join(
            f"{reason}={count}" for reason, count in sorted(filtered_counts.items())
        )
        print(
            f"Filtered {len(filtered_packet_ids)} positions ({summary}).",
            flush=True,
        )

    db.commit()
    return inserted


def main() -> None:
    with sqlite3.connect(DATABASE) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        initialize_parser_state(db)
        corrected = reclassify_legacy_adsb(db)
        if corrected:
            print(
                f"Corrected {corrected} legacy ADS-B protocol labels.",
                flush=True,
            )

        while True:
            inserted = process_pending(db)

            if inserted:
                print(f"Inserted {inserted} positions.", flush=True)

            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
