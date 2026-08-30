import sqlite3
import gc
from contextlib import closing
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import app
import collector
import parser
import retention


class RecordingControlsTest(unittest.TestCase):
    def test_comment_radio_values_are_parsed_independently(self):
        climb, snr, frequency = parser.parse_comment(
            "id06D0264B +020fpm -2.3rot 21.2dB -16.7kHz gps2x3"
        )
        self.assertAlmostEqual(climb, 0.1016)
        self.assertEqual(snr, 21.2)
        self.assertEqual(frequency, -16.7)

    def test_corrected_packet_error_count_does_not_hide_radio_values(self):
        climb, snr, frequency = parser.parse_comment(
            "id06D0264B +317fpm +6.3rot 2.5dB 1e -15.0kHz gps2x2"
        )
        self.assertAlmostEqual(climb, 317 * 0.00508)
        self.assertEqual(snr, 2.5)
        self.assertEqual(frequency, -15.0)

    def test_pilotaware_comment_without_climb_retains_snr_and_frequency(self):
        climb, snr, frequency = parser.parse_comment(
            "id21401D49 10.0dB +8.7kHz"
        )
        self.assertIsNone(climb)
        self.assertEqual(snr, 10.0)
        self.assertEqual(frequency, 8.7)

    def test_pilotaware_transmit_power_is_not_used_as_snr(self):
        climb, snr, frequency = parser.parse_comment(
            "id21401D49 8.5dB +8.6kHz +13.7dBm"
        )
        self.assertIsNone(climb)
        self.assertEqual(snr, 8.5)
        self.assertEqual(frequency, 8.6)

    def test_missing_comment_values_remain_none(self):
        self.assertEqual(parser.parse_comment("id21401D49"), (None, None, None))

    def test_protocol_recognition(self):
        self.assertEqual(parser.protocol_name("OGADSB"), "ADS-B")
        self.assertEqual(parser.protocol_name("ONADSB"), "ADS-B")
        self.assertEqual(parser.protocol_name("OGADSB", "ICA4B1234"), "ADS-B")
        self.assertEqual(parser.protocol_name("OGFLR", "ICA4B1234"), "FLARM")
        self.assertEqual(parser.protocol_name("OGNTRK", "ICA4B1234"), "OGNTRK")
        self.assertEqual(parser.protocol_name("MY-ADSB", "ICA4B1234"), "MY-ADSB")
        self.assertEqual(parser.protocol_name("OGFLR"), "FLARM")

    def test_legacy_adsb_reclassification_is_safe_and_idempotent(self):
        with sqlite3.connect(":memory:") as database:
            database.execute(
                """
                CREATE TABLE positions (
                    id INTEGER PRIMARY KEY,
                    destination TEXT NOT NULL,
                    protocol TEXT
                )
                """
            )
            database.executemany(
                "INSERT INTO positions VALUES (?, ?, ?)",
                (
                    (1, "OGADSB", "ADS-B"),
                    (2, "ONADSB", "ADS-B"),
                    (3, "OGFLR", "ADS-B"),
                    (4, "OGNTRK", "ADS-B"),
                    (5, "MY-ADSB", "ADS-B"),
                    (6, "OGNFNT", "FANET"),
                ),
            )

            self.assertEqual(parser.reclassify_legacy_adsb(database), 3)
            self.assertEqual(parser.reclassify_legacy_adsb(database), 0)
            protocols = dict(
                database.execute("SELECT id, protocol FROM positions").fetchall()
            )

        self.assertEqual(protocols[1], "ADS-B")
        self.assertEqual(protocols[2], "ADS-B")
        self.assertEqual(protocols[3], "FLARM")
        self.assertEqual(protocols[4], "OGNTRK")
        self.assertEqual(protocols[5], "MY-ADSB")
        self.assertEqual(protocols[6], "FANET")

    def test_aprs_tocall_drives_end_to_end_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.sqlite3"
            with patch.object(collector, "DATABASE", database_path):
                database = collector.open_database()
            packets = (
                (
                    1,
                    "2026-08-24T10:00:00+00:00",
                    "ICA34364F",
                    "OGADSB",
                    "APRS <- ICA34364F>OGADSB,qAS,TEST:/140827h4038.69N\\00344.37W^235/248/A=010350 id2534364F +1792fpm",
                ),
                (
                    2,
                    "2026-08-24T10:00:01+00:00",
                    "ICA4B1234",
                    "OGFLR",
                    "APRS <- ICA4B1234>OGFLR,qAS,TEST:/140828h4038.70N\\00344.38W^235/100/A=005000 id254B1234 +0100fpm",
                ),
            )
            database.executemany(
                "INSERT INTO packets VALUES (?, ?, ?, ?, ?)",
                packets,
            )
            parser.initialize_parser_state(database)
            with patch.multiple(
                parser,
                MAX_RADIUS_KM=None,
                MIN_ALTITUDE_M=None,
                MAX_ALTITUDE_M=None,
            ):
                self.assertEqual(parser.process_pending(database), 2)
            protocols = dict(
                database.execute(
                    "SELECT aircraft_id, protocol FROM positions"
                ).fetchall()
            )
            database.close()

        self.assertEqual(protocols["ICA34364F"], "ADS-B")
        self.assertEqual(protocols["ICA4B1234"], "FLARM")

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
