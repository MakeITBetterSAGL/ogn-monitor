import gc
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app


class StatisticsTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        app.DATABASE = Path(self.tempdir.name) / "test.sqlite3"
        app.statistics_cache.clear()
        now = datetime.now(timezone.utc)
        with sqlite3.connect(app.DATABASE) as database:
            database.executescript(
                """
                CREATE TABLE packets (
                    id INTEGER PRIMARY KEY,
                    received_at TEXT NOT NULL
                );
                CREATE TABLE positions (
                    id INTEGER PRIMARY KEY,
                    received_at TEXT NOT NULL,
                    aircraft_id TEXT NOT NULL,
                    protocol TEXT,
                    latitude REAL,
                    longitude REAL,
                    altitude_m REAL,
                    speed_kmh REAL,
                    snr_db REAL
                );
                """
            )
            for index, protocol in enumerate(("FLARM", "FANET", "ADS-L", "ADS-B"), 1):
                received_at = (now - timedelta(minutes=index * 5)).isoformat()
                database.execute(
                    "INSERT INTO packets(id, received_at) VALUES (?, ?)",
                    (index, received_at),
                )
                database.execute(
                    """
                    INSERT INTO positions(
                        id, received_at, aircraft_id, protocol, latitude,
                        longitude, altitude_m, speed_kmh, snr_db
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        index, received_at, f"TEST{index}", protocol,
                        app.STATION_LATITUDE + index * 0.01,
                        app.STATION_LONGITUDE + index * 0.01,
                        1000 + index * 100, 80 + index * 10, 10 + index,
                    ),
                )
        self.client = app.app.test_client()

    def tearDown(self):
        gc.collect()
        self.tempdir.cleanup()

    def test_statistics_api(self):
        response = self.client.get("/api/statistics?range=24h")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["range"], "24h")
        self.assertEqual(payload["bucket_seconds"], 1800)
        self.assertEqual(payload["summary"]["packets"], 4)
        self.assertEqual(payload["summary"]["positions"], 4)
        self.assertEqual(payload["summary"]["aircraft"], 4)
        self.assertTrue(any(row["flarm"] for row in payload["series"]))
        self.assertTrue(any(row["fanet"] for row in payload["series"]))
        self.assertTrue(any(row["adsl"] for row in payload["series"]))
        self.assertTrue(any(row["adsb"] for row in payload["series"]))

    def test_invalid_range(self):
        response = self.client.get("/api/statistics?range=invalid")
        self.assertEqual(response.status_code, 400)

    def test_statistics_page(self):
        response = self.client.get("/stats")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Statistics", response.data)


if __name__ == "__main__":
    unittest.main()
