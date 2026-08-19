import sqlite3
import gc
from contextlib import closing
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import app
import parser
import retention


class RecordingControlsTest(unittest.TestCase):
    def test_protocol_recognition(self):
        self.assertEqual(parser.protocol_name("OGADSB"), "ADS-B")
        self.assertEqual(parser.protocol_name("MY-ADSB"), "ADS-B")
        self.assertEqual(parser.protocol_name("OGNTRK", "ICA4B1234"), "ADS-B")
        self.assertEqual(parser.protocol_name("OGFLR"), "FLARM")

    def test_radius_and_altitude_filters(self):
        with patch.multiple(
            parser,
            STATION_LATITUDE=46.0,
            STATION_LONGITUDE=8.0,
            MAX_RADIUS_KM=10.0,
            MIN_ALTITUDE_M=100.0,
            MAX_ALTITUDE_M=3000.0,
        ):
            self.assertIsNone(parser.filter_reason(46.01, 8.01, 1000))
            self.assertEqual(parser.filter_reason(47.0, 8.0, 1000), "radius")
            self.assertEqual(parser.filter_reason(46.01, 8.01, 50), "minimum altitude")
            self.assertEqual(parser.filter_reason(46.01, 8.01, 4000), "maximum altitude")
            self.assertIsNone(parser.filter_reason(46.01, 8.01, None))

    def test_replay_time_includes_final_minute(self):
        self.assertEqual(app.parse_replay_time("08:30").isoformat(), "08:30:00")
        self.assertEqual(
            app.parse_replay_time("08:30", True).isoformat(),
            "08:30:59.999999",
        )
        self.assertIsNone(app.parse_replay_time("25:00"))

    def test_retention_removes_only_old_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.sqlite3"
            now = datetime(2026, 8, 19, tzinfo=timezone.utc)
            old = (now - timedelta(days=366)).isoformat()
            recent = (now - timedelta(days=10)).isoformat()
            with closing(sqlite3.connect(database_path)) as database:
                database.executescript(
                    """
                    CREATE TABLE packets (id INTEGER PRIMARY KEY, received_at TEXT);
                    CREATE TABLE positions (id INTEGER PRIMARY KEY, packet_id INTEGER, received_at TEXT);
                    """
                )
                database.executemany(
                    "INSERT INTO packets VALUES (?, ?)", ((1, old), (2, recent))
                )
                database.executemany(
                    "INSERT INTO positions VALUES (?, ?, ?)",
                    ((1, 1, old), (2, 2, recent)),
                )
                database.commit()
            with patch.object(retention, "DATABASE", database_path), patch.object(
                retention, "RETENTION_DAYS", 365
            ):
                self.assertEqual(retention.apply_retention(now), (1, 1))
            with closing(sqlite3.connect(database_path)) as database:
                self.assertEqual(database.execute("SELECT COUNT(*) FROM packets").fetchone()[0], 1)
                self.assertEqual(database.execute("SELECT COUNT(*) FROM positions").fetchone()[0], 1)
            gc.collect()


if __name__ == "__main__":
    unittest.main()
