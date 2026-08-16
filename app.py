#!/usr/bin/env python3

from datetime import date, datetime, time, timedelta, timezone
from math import atan2, cos, radians, sin, sqrt
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.getenv("OGN_DATABASE", BASE_DIR / "database" / "ogn.sqlite3"))
OGN_DDB_FILE = BASE_DIR / "database" / "ogn-ddb.json"

STATION_NAME = os.getenv("OGN_STATION_NAME", "My OGN Station")
STATION_LATITUDE = float(os.getenv("OGN_STATION_LATITUDE", "0"))
STATION_LONGITUDE = float(os.getenv("OGN_STATION_LONGITUDE", "0"))
ACTIVE_MINUTES = int(os.getenv("OGN_ACTIVE_MINUTES", "10"))
TRACK_MINUTES = int(os.getenv("OGN_TRACK_MINUTES", "30"))
ONLINE_SECONDS = int(os.getenv("OGN_ONLINE_SECONDS", "120"))
FLIGHT_SESSION_GAP_SECONDS = int(os.getenv("OGN_SESSION_GAP_MINUTES", "20")) * 60
DECODER_HOST = os.getenv("OGN_DECODER_HOST", "127.0.0.1")
DECODER_PORT = int(os.getenv("OGN_DECODER_PORT", "50001"))
STATS_CACHE_SECONDS = 5
SYSTEM_CACHE_SECONDS = 15
HISTORY_CACHE_SECONDS = 60
ARCHIVE_CACHE_SECONDS = 300
COVERAGE_CACHE_SECONDS = 3600
STATISTICS_CACHE_SECONDS = 60
LOCAL_TIMEZONE = ZoneInfo(os.getenv("OGN_TIMEZONE", "UTC"))
AIRCRAFT_ID_RE = re.compile(r"\bid([0-9A-Fa-f]{2})[0-9A-Fa-f]{6}\b")
AIRCRAFT_TYPES = {
    0x0: "unknown",
    0x1: "glider",
    0x2: "tow_plane",
    0x3: "helicopter",
    0x4: "skydiver",
    0x5: "drop_plane",
    0x6: "hang_glider",
    0x7: "paraglider",
    0x8: "powered_aircraft",
    0x9: "jet_aircraft",
    0xA: "unknown",
    0xB: "balloon",
    0xC: "airship",
    0xD: "uav",
    0xE: "unknown",
    0xF: "static_obstacle",
}

app = Flask(__name__)
stats_cache = {"value": None, "expires_at": 0.0}
stats_cache_lock = Lock()
system_cache = {"value": None, "expires_at": 0.0}
system_cache_lock = Lock()
history_cache = {"value": None, "expires_at": 0.0}
history_cache_lock = Lock()
archive_cache = {}
archive_cache_lock = Lock()
coverage_cache = {"value": None, "expires_at": 0.0}
coverage_cache_lock = Lock()
statistics_cache = {}
statistics_cache_lock = Lock()
ddb_cache = {"mtime": None, "devices": {}}
ddb_cache_lock = Lock()


def open_database() -> sqlite3.Connection:
    database = sqlite3.connect(DATABASE, timeout=5)
    database.row_factory = sqlite3.Row
    database.create_function("distance_km", 4, distance_km)
    return database


def distance_km(latitude_1, longitude_1, latitude_2, longitude_2):
    earth_radius_km = 6371.0
    lat1 = radians(latitude_1)
    lat2 = radians(latitude_2)
    delta_lat = radians(latitude_2 - latitude_1)
    delta_lon = radians(longitude_2 - longitude_1)
    value = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    )
    return earth_radius_km * 2 * atan2(sqrt(value), sqrt(1 - value))


def parse_timestamp(value):
    if not value:
        return None
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def receiver_is_online():
    try:
        with socket.create_connection(
            (DECODER_HOST, DECODER_PORT), timeout=0.25
        ):
            return True
    except OSError:
        return False


def utc_cutoff(minutes):
    timestamp = datetime.now(timezone.utc).timestamp() - minutes * 60
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def local_day_start_utc():
    local_now = datetime.now(LOCAL_TIMEZONE)
    local_midnight = local_now.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return local_midnight.astimezone(timezone.utc).isoformat()


def day_bounds_utc(day):
    start = datetime.combine(day, time.min, LOCAL_TIMEZONE)
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).isoformat(),
        end.astimezone(timezone.utc).isoformat(),
    )


def parse_history_date(value):
    try:
        selected = date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    today = datetime.now(LOCAL_TIMEZONE).date()
    if selected > today or selected < today - timedelta(days=3650):
        return None
    return selected


def canonical_aircraft_id(aircraft_id):
    value = str(aircraft_id or "").upper()
    if (
        len(value) == 9
        and value[:3] in {"FNT", "FLR", "ICA"}
        and all(character in "0123456789ABCDEF" for character in value[3:])
    ):
        return value[3:]
    return value


def aircraft_type_from_packet(raw_packet):
    match = AIRCRAFT_ID_RE.search(raw_packet or "")
    if match is None:
        return "unknown"
    details = int(match.group(1), 16)
    return AIRCRAFT_TYPES.get((details >> 2) & 0x0F, "unknown")


def load_ogn_devices():
    try:
        modified = OGN_DDB_FILE.stat().st_mtime
    except OSError:
        return {}
    if ddb_cache["mtime"] == modified:
        return ddb_cache["devices"]
    with ddb_cache_lock:
        if ddb_cache["mtime"] == modified:
            return ddb_cache["devices"]
        try:
            payload = json.loads(OGN_DDB_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ddb_cache["devices"]
        devices = {}
        for device in payload.get("devices", []):
            device_id = str(device.get("device_id", "")).upper()
            device_type = str(device.get("device_type", "")).upper()
            if not device_id or not device_type:
                continue
            devices[(device_type, device_id)] = device
        ddb_cache["mtime"] = modified
        ddb_cache["devices"] = devices
        return devices


def matching_aircraft_devices(aircraft_id, raw_ids):
    identity = canonical_aircraft_id(aircraft_id)
    devices = load_ogn_devices()
    preferred_types = []
    for raw_id in raw_ids:
        prefix = str(raw_id).upper()[:3]
        device_type = {"ICA": "I", "FLR": "F", "FNT": "O", "OGN": "O"}.get(prefix)
        if device_type and device_type not in preferred_types:
            preferred_types.append(device_type)
    candidate_types = preferred_types + [
        value for value in ("I", "F", "O") if value not in preferred_types
    ]
    matches = []
    for device_type in candidate_types:
        device = devices.get((device_type, identity))
        if not device or str(device.get("identified", "N")).upper() != "Y":
            continue
        matches.append(
            {
                "device_type": device_type,
                "device_id": identity,
                "aircraft_model": device.get("aircraft_model") or None,
                "registration": device.get("registration") or None,
                "competition_id": device.get("cn") or None,
                "tracked": str(device.get("tracked", "N")).upper() == "Y",
                "identified": True,
            }
        )
    return matches, preferred_types


def get_aircraft_metadata(aircraft_id, raw_ids):
    identity = canonical_aircraft_id(aircraft_id)
    devices = load_ogn_devices()
    matches, preferred_types = matching_aircraft_devices(identity, raw_ids)
    is_icao = "I" in preferred_types
    sources = [
        {"name": "OGN Devices Database", "url": "https://ddb.glidernet.org/"}
    ]
    if is_icao:
        sources.append(
            {
                "name": "OpenSky aircraft profile",
                "url": f"https://old.opensky-network.org/aircraft-profile?icao24={identity.lower()}",
            }
        )
        if identity.upper().startswith("4B"):
            sources.append(
                {
                    "name": "Swiss Aircraft Register (FOCA)",
                    "url": "https://app02.bazl.admin.ch/web/bazl/en/",
                }
            )
    return {
        "aircraft_id": identity,
        "records": matches,
        "sources": sources,
        "database_available": bool(devices),
    }


def calculate_stats():
    with open_database() as db:
        row = db.execute(
            """
            SELECT
                COUNT(*) AS total_packets,
                MAX(received_at) AS last_packet
            FROM packets
            """
        ).fetchone()

        aircraft_today = db.execute(
            """
            SELECT COUNT(DISTINCT CASE
                WHEN length(sender) = 9
                 AND substr(sender, 1, 3) IN ('FNT', 'FLR', 'ICA')
                THEN substr(sender, 4)
                ELSE sender
            END) AS count
            FROM packets
            WHERE destination != 'OGNSDR'
              AND received_at >= ?
            """,
            (local_day_start_utc(),),
        ).fetchone()["count"]

        active_aircraft = db.execute(
            """
            SELECT COUNT(DISTINCT CASE
                WHEN length(aircraft_id) = 9
                 AND substr(aircraft_id, 1, 3) IN ('FNT', 'FLR', 'ICA')
                THEN substr(aircraft_id, 4)
                ELSE aircraft_id
            END) AS count
            FROM positions
            WHERE received_at >= ?
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
            """,
            (utc_cutoff(ACTIVE_MINUTES),),
        ).fetchone()["count"]

    last_packet = parse_timestamp(row["last_packet"])
    seconds_since_last = None
    if last_packet is not None:
        seconds_since_last = max(
            0, int((datetime.now(timezone.utc) - last_packet).total_seconds())
        )

    active_traffic = (
        seconds_since_last is not None
        and seconds_since_last < ONLINE_SECONDS
    )
    receiver_online = receiver_is_online()

    return {
        "total_packets": row["total_packets"],
        "aircraft_today": aircraft_today,
        "active_aircraft": active_aircraft,
        "last_packet": last_packet.isoformat() if last_packet else None,
        "seconds_since_last": seconds_since_last,
        "online": receiver_online,
        "receiver_online": receiver_online,
        "active_traffic": active_traffic,
    }


def get_stats():
    now = monotonic()
    if stats_cache["value"] is not None and now < stats_cache["expires_at"]:
        return stats_cache["value"]

    with stats_cache_lock:
        now = monotonic()
        if stats_cache["value"] is None or now >= stats_cache["expires_at"]:
            stats_cache["value"] = calculate_stats()
            stats_cache["expires_at"] = now + STATS_CACHE_SECONDS
        return stats_cache["value"]


def process_running(command_fragment):
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ")
            if command_fragment.encode() in command:
                return True
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return False


def calculate_system_health():
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        memory[key] = int(value.strip().split()[0]) * 1024

    temperature_path = Path("/sys/class/thermal/thermal_zone0/temp")
    temperature_c = (
        round(int(temperature_path.read_text().strip()) / 1000, 1)
        if temperature_path.exists() else None
    )
    disk = shutil.disk_usage(BASE_DIR)
    uptime_seconds = int(float(Path("/proc/uptime").read_text().split()[0]))
    load_1, load_5, load_15 = os.getloadavg()

    return {
        "temperature_c": temperature_c,
        "load": {
            "one_minute": round(load_1, 2),
            "five_minutes": round(load_5, 2),
            "fifteen_minutes": round(load_15, 2),
            "cpu_count": os.cpu_count() or 1,
        },
        "memory": {
            "total_bytes": memory.get("MemTotal", 0),
            "available_bytes": memory.get("MemAvailable", 0),
        },
        "disk": {
            "total_bytes": disk.total,
            "free_bytes": disk.free,
        },
        "uptime_seconds": uptime_seconds,
        "database_bytes": DATABASE.stat().st_size if DATABASE.exists() else 0,
        "services": {
            "decoder": receiver_is_online(),
            "collector": process_running(str(BASE_DIR / "collector.py")),
            "monitor": True,
        },
    }


def get_system_health():
    now = monotonic()
    if system_cache["value"] is not None and now < system_cache["expires_at"]:
        return system_cache["value"]

    with system_cache_lock:
        now = monotonic()
        if system_cache["value"] is None or now >= system_cache["expires_at"]:
            system_cache["value"] = calculate_system_health()
            system_cache["expires_at"] = now + SYSTEM_CACHE_SECONDS
        return system_cache["value"]


def calculate_today_activity():
    day_start = local_day_start_utc()
    with open_database() as db:
        packet_summary = db.execute(
            """
            SELECT COUNT(*) AS packets, COUNT(DISTINCT CASE
                WHEN destination != 'OGNSDR' THEN
                    CASE WHEN length(sender) = 9
                           AND substr(sender, 1, 3) IN ('FNT', 'FLR', 'ICA')
                         THEN substr(sender, 4) ELSE sender END
            END) AS aircraft
            FROM packets
            WHERE received_at >= ?
            """,
            (day_start,),
        ).fetchone()
        hourly_rows = db.execute(
            """
            SELECT strftime('%H', received_at, 'localtime') AS hour,
                   COUNT(*) AS packets
            FROM packets
            WHERE received_at >= ?
            GROUP BY hour
            ORDER BY hour
            """,
            (day_start,),
        ).fetchall()
        position_summary = db.execute(
            """
            SELECT MAX(altitude_m) AS max_altitude_m,
                   MAX(speed_kmh) AS max_speed_kmh
            FROM positions
            WHERE received_at >= ?
            """,
            (day_start,),
        ).fetchone()
        protocol_rows = db.execute(
            """
            SELECT COALESCE(protocol, 'OTHER') AS protocol,
                   COUNT(DISTINCT CASE
                       WHEN length(aircraft_id) = 9
                        AND substr(aircraft_id, 1, 3) IN ('FNT', 'FLR', 'ICA')
                       THEN substr(aircraft_id, 4)
                       ELSE aircraft_id
                   END) AS aircraft
            FROM positions
            WHERE received_at >= ?
            GROUP BY COALESCE(protocol, 'OTHER')
            ORDER BY aircraft DESC
            """,
            (day_start,),
        ).fetchall()
        coordinate_rows = db.execute(
            """
            SELECT latitude, longitude
            FROM positions
            WHERE received_at >= ?
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
            """,
            (day_start,),
        ).fetchall()

    max_distance = 0.0
    for row in coordinate_rows:
        max_distance = max(
            max_distance,
            distance_km(
                STATION_LATITUDE, STATION_LONGITUDE,
                row["latitude"], row["longitude"],
            ),
        )
    hourly = {int(row["hour"]): row["packets"] for row in hourly_rows}
    return {
        "packets": packet_summary["packets"],
        "aircraft": packet_summary["aircraft"],
        "max_altitude_m": position_summary["max_altitude_m"],
        "max_speed_kmh": position_summary["max_speed_kmh"],
        "max_distance_km": round(max_distance, 1) if coordinate_rows else None,
        "protocols": [dict(row) for row in protocol_rows],
        "hourly_packets": [
            {"hour": hour, "packets": hourly.get(hour, 0)}
            for hour in range(24)
        ],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_today_activity():
    now = monotonic()
    if history_cache["value"] is not None and now < history_cache["expires_at"]:
        return history_cache["value"]

    with history_cache_lock:
        now = monotonic()
        if history_cache["value"] is None or now >= history_cache["expires_at"]:
            history_cache["value"] = calculate_today_activity()
            history_cache["expires_at"] = now + HISTORY_CACHE_SECONDS
        return history_cache["value"]


def calculate_archive_days(days):
    today = datetime.now(LOCAL_TIMEZONE).date()
    first_day = today - timedelta(days=days - 1)
    start_utc, _ = day_bounds_utc(first_day)
    with open_database() as db:
        packet_rows = db.execute(
            """
            SELECT date(received_at, 'localtime') AS day,
                   COUNT(*) AS packets,
                   COUNT(DISTINCT CASE
                       WHEN destination != 'OGNSDR' THEN
                           CASE WHEN length(sender) = 9
                                  AND substr(sender, 1, 3) IN ('FNT', 'FLR', 'ICA')
                                THEN substr(sender, 4) ELSE sender END
                   END) AS aircraft
            FROM packets
            WHERE received_at >= ?
            GROUP BY day
            """,
            (start_utc,),
        ).fetchall()
        position_rows = db.execute(
            """
            SELECT date(received_at, 'localtime') AS day,
                   MAX(altitude_m) AS max_altitude_m,
                   MAX(speed_kmh) AS max_speed_kmh
            FROM positions
            WHERE received_at >= ?
            GROUP BY day
            """,
            (start_utc,),
        ).fetchall()
        coordinate_rows = db.execute(
            """
            SELECT date(received_at, 'localtime') AS day, latitude, longitude
            FROM positions
            WHERE received_at >= ? AND latitude IS NOT NULL AND longitude IS NOT NULL
            """,
            (start_utc,),
        ).fetchall()

    packets = {row["day"]: dict(row) for row in packet_rows}
    positions = {row["day"]: dict(row) for row in position_rows}
    max_distances = {}
    for row in coordinate_rows:
        value = distance_km(
            STATION_LATITUDE, STATION_LONGITUDE,
            row["latitude"], row["longitude"],
        )
        max_distances[row["day"]] = max(max_distances.get(row["day"], 0), value)
    result = []
    for offset in range(days):
        day_value = first_day + timedelta(days=offset)
        key = day_value.isoformat()
        packet = packets.get(key, {})
        position = positions.get(key, {})
        result.append(
            {
                "date": key,
                "packets": packet.get("packets", 0),
                "aircraft": packet.get("aircraft", 0),
                "max_altitude_m": position.get("max_altitude_m"),
                "max_speed_kmh": position.get("max_speed_kmh"),
                "max_distance_km": round(max_distances[key], 1) if key in max_distances else None,
            }
        )
    return list(reversed(result))


def calculate_archive_day(selected_day):
    start_utc, end_utc = day_bounds_utc(selected_day)
    with open_database() as db:
        aircraft_rows = db.execute(
            """
            SELECT p.aircraft_id, COALESCE(p.protocol, 'OTHER') AS protocol,
                   p.received_at, p.latitude, p.longitude, p.altitude_m,
                   p.speed_kmh, p.snr_db, source_packet.raw_packet
            FROM positions AS p
            INNER JOIN packets AS source_packet ON source_packet.id = p.packet_id
            WHERE p.received_at >= ? AND p.received_at < ?
            ORDER BY p.received_at
            """,
            (start_utc, end_utc),
        ).fetchall()
        protocol_rows = db.execute(
            """
            SELECT COALESCE(protocol, 'OTHER') AS protocol,
                   COUNT(DISTINCT CASE
                       WHEN length(aircraft_id) = 9
                        AND substr(aircraft_id, 1, 3) IN ('FNT', 'FLR', 'ICA')
                       THEN substr(aircraft_id, 4)
                       ELSE aircraft_id
                   END) AS aircraft
            FROM positions
            WHERE received_at >= ? AND received_at < ?
            GROUP BY COALESCE(protocol, 'OTHER')
            ORDER BY aircraft DESC
            """,
            (start_utc, end_utc),
        ).fetchall()
        coordinate_rows = db.execute(
            """
            SELECT latitude, longitude
            FROM positions
            WHERE received_at >= ? AND received_at < ?
              AND latitude IS NOT NULL AND longitude IS NOT NULL
            """,
            (start_utc, end_utc),
        ).fetchall()

    max_distance = None
    if coordinate_rows:
        max_distance = round(
            max(
                distance_km(
                    STATION_LATITUDE, STATION_LONGITUDE,
                    row["latitude"], row["longitude"],
                )
                for row in coordinate_rows
            ),
            1,
        )
    sessions_by_identity = {}
    for row in aircraft_rows:
        raw_id = row["aircraft_id"]
        identity = canonical_aircraft_id(raw_id)
        received = parse_timestamp(row["received_at"])
        identity_sessions = sessions_by_identity.setdefault(identity, [])
        session = identity_sessions[-1] if identity_sessions else None
        if (
            session is None
            or (received - session["_last_timestamp"]).total_seconds()
            > FLIGHT_SESSION_GAP_SECONDS
        ):
            session = {
                "aircraft_id": identity,
                "raw_aircraft_ids": set(),
                "protocols": set(),
                "aircraft_types": set(),
                "first_received": row["received_at"],
                "last_received": row["received_at"],
                "position_points": 0,
                "max_altitude_m": None,
                "max_speed_kmh": None,
                "max_snr_db": None,
                "min_distance_km": None,
                "max_distance_km": None,
                "_first_timestamp": received,
                "_last_timestamp": received,
            }
            identity_sessions.append(session)
        session["raw_aircraft_ids"].add(raw_id)
        session["protocols"].add(row["protocol"])
        session["aircraft_types"].add(
            aircraft_type_from_packet(row["raw_packet"])
        )
        session["last_received"] = row["received_at"]
        session["_last_timestamp"] = received
        session["position_points"] += 1
        for source, target in (
            ("altitude_m", "max_altitude_m"),
            ("speed_kmh", "max_speed_kmh"),
            ("snr_db", "max_snr_db"),
        ):
            value = row[source]
            if value is not None:
                current = session[target]
                session[target] = value if current is None else max(current, value)
        if row["latitude"] is not None and row["longitude"] is not None:
            value = distance_km(
                STATION_LATITUDE, STATION_LONGITUDE,
                row["latitude"], row["longitude"],
            )
            current = session["max_distance_km"]
            session["max_distance_km"] = (
                value if current is None else max(current, value)
            )
            current = session["min_distance_km"]
            session["min_distance_km"] = (
                value if current is None else min(current, value)
            )

    aircraft = []
    for identity_sessions in sessions_by_identity.values():
        for session_number, session in enumerate(identity_sessions, start=1):
            session["duration_seconds"] = max(
                0,
                int(
                    (session["_last_timestamp"] - session["_first_timestamp"])
                    .total_seconds()
                ),
            )
            session["session_number"] = session_number
            session["protocols"] = sorted(session["protocols"])
            session["protocol"] = " / ".join(session["protocols"])
            known_types = sorted(
                value for value in session["aircraft_types"]
                if value != "unknown"
            )
            session["aircraft_type"] = (
                known_types[0] if known_types else "unknown"
            )
            metadata_records, _ = matching_aircraft_devices(
                session["aircraft_id"], session["raw_aircraft_ids"]
            )
            session["has_metadata"] = bool(metadata_records)
            session["max_distance_km"] = (
                round(session["max_distance_km"], 1)
                if session["max_distance_km"] is not None else None
            )
            session["min_distance_km"] = (
                round(session["min_distance_km"], 1)
                if session["min_distance_km"] is not None else None
            )
            session["raw_aircraft_ids"] = sorted(session["raw_aircraft_ids"])
            del session["aircraft_types"]
            del session["_first_timestamp"]
            del session["_last_timestamp"]
            aircraft.append(session)
    aircraft.sort(key=lambda item: item["first_received"])
    return {
        "date": selected_day.isoformat(),
        "aircraft": aircraft,
        "protocols": [dict(row) for row in protocol_rows],
        "max_distance_km": max_distance,
    }


def get_cached_archive(key, calculator):
    now = monotonic()
    cached = archive_cache.get(key)
    if cached and now < cached["expires_at"]:
        return cached["value"]
    with archive_cache_lock:
        cached = archive_cache.get(key)
        if not cached or now >= cached["expires_at"]:
            cached = {
                "value": calculator(),
                "expires_at": now + ARCHIVE_CACHE_SECONDS,
            }
            archive_cache[key] = cached
        return cached["value"]


def get_active_aircraft():
    with open_database() as db:
        rows = db.execute(
            """
            SELECT
                p.aircraft_id, p.protocol, p.received_at,
                p.latitude, p.longitude, p.altitude_m,
                p.course_deg, p.speed_kmh, p.climb_ms,
                p.snr_db, p.frequency_offset_khz,
                source_packet.raw_packet
            FROM positions AS p
            INNER JOIN (
                SELECT aircraft_id, MAX(id) AS latest_id
                FROM positions
                WHERE received_at >= ?
                  AND latitude IS NOT NULL
                  AND longitude IS NOT NULL
                GROUP BY aircraft_id
            ) AS latest ON p.id = latest.latest_id
            INNER JOIN packets AS source_packet ON source_packet.id = p.packet_id
            ORDER BY p.received_at DESC
            """,
            (utc_cutoff(ACTIVE_MINUTES),),
        ).fetchall()

    identities = {}
    for row in rows:
        latitude = row["latitude"]
        longitude = row["longitude"]
        raw_id = row["aircraft_id"]
        identity = canonical_aircraft_id(raw_id)
        protocol = row["protocol"] or "OTHER"
        candidate = {
            "aircraft_id": identity,
            "raw_aircraft_ids": [raw_id],
            "protocol": protocol,
            "protocols": [protocol],
            "received_at": row["received_at"],
            "latitude": latitude,
            "longitude": longitude,
            "altitude_m": row["altitude_m"],
            "course_deg": row["course_deg"],
            "speed_kmh": row["speed_kmh"],
            "climb_ms": row["climb_ms"],
            "snr_db": row["snr_db"],
            "frequency_offset_khz": row["frequency_offset_khz"],
            "aircraft_type": aircraft_type_from_packet(row["raw_packet"]),
            "distance_km": round(
                distance_km(
                    STATION_LATITUDE, STATION_LONGITUDE,
                    latitude, longitude,
                ),
                1,
            ),
        }
        existing = identities.get(identity)
        if existing is None:
            identities[identity] = candidate
            continue
        raw_ids = sorted(set(existing["raw_aircraft_ids"] + [raw_id]))
        protocols = sorted(set(existing["protocols"] + [protocol]))
        if row["received_at"] > existing["received_at"]:
            candidate["raw_aircraft_ids"] = raw_ids
            candidate["protocols"] = protocols
            identities[identity] = candidate
        else:
            existing["raw_aircraft_ids"] = raw_ids
            existing["protocols"] = protocols
    return sorted(
        identities.values(), key=lambda item: item["received_at"], reverse=True
    )


def get_tracks():
    with open_database() as db:
        rows = db.execute(
            """
            SELECT aircraft_id, protocol, received_at, latitude, longitude
            FROM positions
            WHERE received_at >= ?
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
            ORDER BY aircraft_id, received_at, id
            """,
            (utc_cutoff(TRACK_MINUTES),),
        ).fetchall()

    tracks = {}
    for row in rows:
        raw_id = row["aircraft_id"]
        identity = canonical_aircraft_id(raw_id)
        protocol = row["protocol"] or "OTHER"
        track = tracks.setdefault(
            identity,
            {
                "protocol": protocol,
                "protocols": set(),
                "raw_aircraft_ids": set(),
                "latest_received_at": "",
                "points": [],
            },
        )
        track["protocols"].add(protocol)
        track["raw_aircraft_ids"].add(raw_id)
        if row["received_at"] > track["latest_received_at"]:
            track["latest_received_at"] = row["received_at"]
            track["protocol"] = protocol
        track["points"].append(
            {
                "received_at": row["received_at"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
            }
        )
    for track in tracks.values():
        track["points"].sort(key=lambda point: point["received_at"])
        track["protocols"] = sorted(track["protocols"])
        track["raw_aircraft_ids"] = sorted(track["raw_aircraft_ids"])
        del track["latest_received_at"]
    return tracks


def get_replay_session(selected_day, aircraft_id, session_number):
    start_utc, end_utc = day_bounds_utc(selected_day)
    identity = canonical_aircraft_id(aircraft_id)
    with open_database() as db:
        rows = db.execute(
            """
            SELECT p.aircraft_id, COALESCE(p.protocol, 'OTHER') AS protocol,
                   p.received_at, p.latitude, p.longitude, p.altitude_m,
                   p.course_deg, p.speed_kmh, p.climb_ms, p.snr_db,
                   source_packet.raw_packet
            FROM positions AS p
            INNER JOIN packets AS source_packet ON source_packet.id = p.packet_id
            WHERE p.received_at >= ? AND p.received_at < ?
              AND p.latitude IS NOT NULL AND p.longitude IS NOT NULL
            ORDER BY p.received_at, p.id
            """,
            (start_utc, end_utc),
        ).fetchall()

    sessions = []
    current = []
    last_timestamp = None
    for row in rows:
        if canonical_aircraft_id(row["aircraft_id"]) != identity:
            continue
        timestamp = parse_timestamp(row["received_at"])
        if (
            current
            and (timestamp - last_timestamp).total_seconds()
            > FLIGHT_SESSION_GAP_SECONDS
        ):
            sessions.append(current)
            current = []
        current.append(row)
        last_timestamp = timestamp
    if current:
        sessions.append(current)
    if session_number < 1 or session_number > len(sessions):
        return None

    selected = sessions[session_number - 1]
    # Preserve high-resolution tracks for ordinary sessions. This safety cap
    # only protects the browser from exceptionally large sessions.
    maximum_points = 10000
    step = max(1, (len(selected) + maximum_points - 1) // maximum_points)
    sampled = list(selected[::step])
    if sampled[-1]["received_at"] != selected[-1]["received_at"]:
        sampled.append(selected[-1])
    points = [
        {
            "received_at": row["received_at"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "altitude_m": row["altitude_m"],
            "course_deg": row["course_deg"],
            "speed_kmh": row["speed_kmh"],
            "climb_ms": row["climb_ms"],
            "snr_db": row["snr_db"],
            "protocol": row["protocol"],
            "raw_aircraft_id": row["aircraft_id"],
            "aircraft_type": aircraft_type_from_packet(row["raw_packet"]),
        }
        for row in sampled
    ]
    intervals = [
        (parse_timestamp(selected[index]["received_at"]) -
         parse_timestamp(selected[index - 1]["received_at"])).total_seconds()
        for index in range(1, len(selected))
    ]
    return {
        "date": selected_day.isoformat(),
        "aircraft_id": identity,
        "session_number": session_number,
        "source_points": len(selected),
        "displayed_points": len(points),
        "sampled": step > 1,
        "average_interval_seconds": (
            round(sum(intervals) / len(intervals), 2) if intervals else None
        ),
        "minimum_interval_seconds": (
            round(min(intervals), 2) if intervals else None
        ),
        "points": points,
    }


def calculate_coverage():
    with open_database() as db:
        summary = db.execute(
            """
            SELECT COUNT(*) AS position_points,
                   COUNT(DISTINCT aircraft_id) AS source_aircraft,
                   MIN(received_at) AS first_received,
                   MAX(received_at) AS last_received,
                   MIN(altitude_m) AS min_altitude_m,
                   MAX(altitude_m) AS max_altitude_m
            FROM positions
            WHERE latitude BETWEEN -90 AND 90
              AND longitude BETWEEN -180 AND 180
            """
        ).fetchone()
        rows = db.execute(
            """
            SELECT AVG(latitude) AS latitude,
                   AVG(longitude) AS longitude,
                   COUNT(*) AS points,
                   AVG(altitude_m) AS avg_altitude_m,
                   MIN(altitude_m) AS min_altitude_m,
                   MAX(altitude_m) AS max_altitude_m
            FROM positions
            WHERE latitude BETWEEN -90 AND 90
              AND longitude BETWEEN -180 AND 180
            GROUP BY ROUND(latitude / 0.00135),
                     ROUND(longitude / 0.00195)
            ORDER BY points DESC
            """
        ).fetchall()
    return {
        "position_points": summary["position_points"],
        "source_aircraft": summary["source_aircraft"],
        "first_received": summary["first_received"],
        "last_received": summary["last_received"],
        "min_altitude_m": summary["min_altitude_m"],
        "max_altitude_m": summary["max_altitude_m"],
        "resolution_meters": 150,
        "cells": [dict(row) for row in rows],
    }


def get_coverage():
    now = monotonic()
    if coverage_cache["value"] is not None and now < coverage_cache["expires_at"]:
        return coverage_cache["value"]
    with coverage_cache_lock:
        now = monotonic()
        if coverage_cache["value"] is None or now >= coverage_cache["expires_at"]:
            coverage_cache["value"] = calculate_coverage()
            coverage_cache["expires_at"] = now + COVERAGE_CACHE_SECONDS
        return coverage_cache["value"]


STATISTICS_RANGES = {
    "2h": (timedelta(hours=2), 5 * 60),
    "8h": (timedelta(hours=8), 15 * 60),
    "24h": (timedelta(hours=24), 30 * 60),
    "7d": (timedelta(days=7), 2 * 60 * 60),
    "30d": (timedelta(days=30), 6 * 60 * 60),
    "90d": (timedelta(days=90), 24 * 60 * 60),
    "1y": (timedelta(days=365), 24 * 60 * 60),
}


def rounded(value, digits=1):
    return round(value, digits) if value is not None else None


def calculate_statistics(range_key):
    duration, bucket_seconds = STATISTICS_RANGES[range_key]
    now = datetime.now(timezone.utc)
    cutoff = now - duration
    cutoff_iso = cutoff.isoformat()

    with open_database() as db:
        packet_rows = db.execute(
            """
            SELECT
                (CAST(strftime('%s', received_at) AS INTEGER) / ?) * ? AS bucket,
                COUNT(*) AS packets
            FROM packets
            WHERE received_at >= ?
            GROUP BY bucket
            ORDER BY bucket
            """,
            (bucket_seconds, bucket_seconds, cutoff_iso),
        ).fetchall()

        position_rows = db.execute(
            """
            SELECT
                (CAST(strftime('%s', received_at) AS INTEGER) / ?) * ? AS bucket,
                COUNT(*) AS positions,
                COUNT(DISTINCT aircraft_id) AS aircraft,
                AVG(distance_km(?, ?, latitude, longitude)) AS avg_distance_km,
                MAX(distance_km(?, ?, latitude, longitude)) AS max_distance_km,
                AVG(snr_db) AS avg_snr_db,
                MAX(snr_db) AS max_snr_db,
                AVG(altitude_m) AS avg_altitude_m,
                MAX(altitude_m) AS max_altitude_m,
                AVG(speed_kmh) AS avg_speed_kmh,
                MAX(speed_kmh) AS max_speed_kmh,
                SUM(CASE WHEN protocol = 'FLARM' THEN 1 ELSE 0 END) AS flarm,
                SUM(CASE WHEN protocol = 'FANET' THEN 1 ELSE 0 END) AS fanet,
                SUM(CASE WHEN protocol = 'ADS-L' THEN 1 ELSE 0 END) AS adsl,
                SUM(CASE WHEN protocol NOT IN ('FLARM', 'FANET', 'ADS-L')
                         OR protocol IS NULL THEN 1 ELSE 0 END) AS other
            FROM positions
            WHERE received_at >= ?
              AND latitude BETWEEN -90 AND 90
              AND longitude BETWEEN -180 AND 180
            GROUP BY bucket
            ORDER BY bucket
            """,
            (
                bucket_seconds, bucket_seconds,
                STATION_LATITUDE, STATION_LONGITUDE,
                STATION_LATITUDE, STATION_LONGITUDE,
                cutoff_iso,
            ),
        ).fetchall()

        summary = db.execute(
            """
            SELECT
                COUNT(*) AS positions,
                COUNT(DISTINCT aircraft_id) AS aircraft,
                MAX(distance_km(?, ?, latitude, longitude)) AS max_distance_km,
                AVG(snr_db) AS avg_snr_db,
                MAX(altitude_m) AS max_altitude_m,
                MAX(speed_kmh) AS max_speed_kmh
            FROM positions
            WHERE received_at >= ?
              AND latitude BETWEEN -90 AND 90
              AND longitude BETWEEN -180 AND 180
            """,
            (STATION_LATITUDE, STATION_LONGITUDE, cutoff_iso),
        ).fetchone()

    packets_by_bucket = {
        int(row["bucket"]): row["packets"] for row in packet_rows
        if row["bucket"] is not None
    }
    positions_by_bucket = {
        int(row["bucket"]): row for row in position_rows
        if row["bucket"] is not None
    }
    first_bucket = int(cutoff.timestamp()) // bucket_seconds * bucket_seconds
    last_bucket = int(now.timestamp()) // bucket_seconds * bucket_seconds
    series = []
    packet_total = 0

    for bucket in range(first_bucket, last_bucket + 1, bucket_seconds):
        row = positions_by_bucket.get(bucket)
        packets = packets_by_bucket.get(bucket, 0)
        packet_total += packets
        series.append(
            {
                "timestamp": datetime.fromtimestamp(bucket, timezone.utc).isoformat(),
                "packets": packets,
                "positions": row["positions"] if row else 0,
                "aircraft": row["aircraft"] if row else 0,
                "avg_distance_km": rounded(row["avg_distance_km"]) if row else None,
                "max_distance_km": rounded(row["max_distance_km"]) if row else None,
                "avg_snr_db": rounded(row["avg_snr_db"]) if row else None,
                "max_snr_db": rounded(row["max_snr_db"]) if row else None,
                "avg_altitude_m": rounded(row["avg_altitude_m"], 0) if row else None,
                "max_altitude_m": rounded(row["max_altitude_m"], 0) if row else None,
                "avg_speed_kmh": rounded(row["avg_speed_kmh"], 0) if row else None,
                "max_speed_kmh": rounded(row["max_speed_kmh"], 0) if row else None,
                "flarm": row["flarm"] if row else 0,
                "fanet": row["fanet"] if row else 0,
                "adsl": row["adsl"] if row else 0,
                "other": row["other"] if row else 0,
            }
        )

    return {
        "range": range_key,
        "bucket_seconds": bucket_seconds,
        "generated_at": now.isoformat(),
        "summary": {
            "packets": packet_total,
            "positions": summary["positions"],
            "aircraft": summary["aircraft"],
            "max_distance_km": rounded(summary["max_distance_km"]),
            "avg_snr_db": rounded(summary["avg_snr_db"]),
            "max_altitude_m": rounded(summary["max_altitude_m"], 0),
            "max_speed_kmh": rounded(summary["max_speed_kmh"], 0),
        },
        "series": series,
    }


def get_statistics(range_key):
    now = monotonic()
    cached = statistics_cache.get(range_key)
    if cached and now < cached["expires_at"]:
        return cached["value"]
    with statistics_cache_lock:
        now = monotonic()
        cached = statistics_cache.get(range_key)
        if not cached or now >= cached["expires_at"]:
            cached = {
                "value": calculate_statistics(range_key),
                "expires_at": now + STATISTICS_CACHE_SECONDS,
            }
            statistics_cache[range_key] = cached
        return cached["value"]


@app.after_request
def disable_api_cache(response):
    if response.content_type.startswith("application/json"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/")
def index():
    return render_template(
        "index.html",
        stats=get_stats(),
        station={
            "name": STATION_NAME,
            "latitude": STATION_LATITUDE,
            "longitude": STATION_LONGITUDE,
            "timezone": str(LOCAL_TIMEZONE),
        },
    )


@app.route("/stats")
def statistics():
    return render_template(
        "stats.html",
        station={"name": STATION_NAME, "timezone": str(LOCAL_TIMEZONE)},
    )


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


@app.route("/api/statistics")
def api_statistics():
    range_key = request.args.get("range", "24h")
    if range_key not in STATISTICS_RANGES:
        return jsonify({"error": "Invalid statistics range"}), 400
    return jsonify(get_statistics(range_key))


@app.route("/api/aircraft")
def api_aircraft():
    return jsonify(
        {
            "station": {
                "name": STATION_NAME,
                "latitude": STATION_LATITUDE,
                "longitude": STATION_LONGITUDE,
            },
            "active_minutes": ACTIVE_MINUTES,
            "aircraft": get_active_aircraft(),
        }
    )


@app.route("/api/system")
def api_system():
    return jsonify(get_system_health())


@app.route("/api/history/today")
def api_history_today():
    return jsonify(get_today_activity())


@app.route("/api/history/days")
def api_history_days():
    try:
        days = int(request.args.get("days", "7"))
    except ValueError:
        days = 7
    days = 30 if days > 7 else 7
    return jsonify(
        {
            "days": days,
            "history": get_cached_archive(
                f"days:{days}", lambda: calculate_archive_days(days)
            ),
        }
    )


@app.route("/api/history/day")
def api_history_day():
    selected_day = parse_history_date(request.args.get("date"))
    if selected_day is None:
        return jsonify({"error": "Invalid history date"}), 400
    return jsonify(
        get_cached_archive(
            f"day:{selected_day.isoformat()}",
            lambda: calculate_archive_day(selected_day),
        )
    )


@app.route("/api/tracks")
def api_tracks():
    return jsonify({"track_minutes": TRACK_MINUTES, "tracks": get_tracks()})


@app.route("/api/replay/session")
def api_replay_session():
    selected_day = parse_history_date(request.args.get("date"))
    aircraft_id = request.args.get("aircraft_id", "").strip()
    try:
        session_number = int(request.args.get("session", "1"))
    except ValueError:
        session_number = 0
    if selected_day is None or not aircraft_id or session_number < 1:
        return jsonify({"error": "Invalid replay session"}), 400
    replay = get_replay_session(selected_day, aircraft_id, session_number)
    if replay is None:
        return jsonify({"error": "Replay session not found"}), 404
    return jsonify(replay)


@app.route("/api/coverage")
def api_coverage():
    return jsonify(get_coverage())


@app.route("/api/aircraft/metadata")
def api_aircraft_metadata():
    aircraft_id = request.args.get("aircraft_id", "").strip()
    raw_ids = [
        value.strip() for value in request.args.get("raw_ids", "").split(",")
        if value.strip()
    ]
    if not aircraft_id or len(aircraft_id) > 16 or len(raw_ids) > 10:
        return jsonify({"error": "Invalid aircraft identifier"}), 400
    return jsonify(get_aircraft_metadata(aircraft_id, raw_ids))


if __name__ == "__main__":
    app.run(
        host=os.getenv("OGN_WEB_HOST", "0.0.0.0"),
        port=int(os.getenv("OGN_WEB_PORT", "5000")),
        debug=False,
    )
