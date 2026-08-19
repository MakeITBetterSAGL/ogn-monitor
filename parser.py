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
    "OGADSB": "ADS-B",
}


def protocol_name(destination: str, sender: str = "") -> str:
    normalized = (destination or "").strip().upper()
    # OGN uses the ICA prefix for ICAO/transponder targets. This remains
    # identifiable even when all traffic shares a generic APRS destination.
    if (sender or "").strip().upper().startswith("ICA"):
        return "ADS-B"
    if normalized in PROTOCOLS:
        return PROTOCOLS[normalized]
    if "ADSB" in normalized.replace("-", ""):
        return "ADS-B"
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

        while True:
            inserted = process_pending(db)

            if inserted:
                print(f"Inserted {inserted} positions.", flush=True)

            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
