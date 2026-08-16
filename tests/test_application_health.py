import gc
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import app


class ApplicationHealthTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_database = app.DATABASE
        self.original_runtime_mode = app.RUNTIME_MODE
        app.DATABASE = Path(self.tempdir.name) / "ogn.sqlite3"
        app.RUNTIME_MODE = "docker"
        app.system_cache["value"] = None
        app.system_cache["expires_at"] = 0
        with sqlite3.connect(app.DATABASE) as database:
            database.executescript(
                """
                CREATE TABLE packets (
                    id INTEGER PRIMARY KEY,
                    received_at TEXT NOT NULL
                );
                CREATE TABLE parser_state (
                    name TEXT PRIMARY KEY,
                    last_packet_id INTEGER NOT NULL
                );
                """
            )
            database.execute(
                "INSERT INTO packets(id, received_at) VALUES (?, ?)",
                (5, datetime.now(timezone.utc).isoformat()),
            )
            database.execute(
                "INSERT INTO parser_state(name, last_packet_id) VALUES (?, ?)",
                ("positions", 3),
            )
        self.client = app.app.test_client()

    def tearDown(self):
        app.DATABASE = self.original_database
        app.RUNTIME_MODE = self.original_runtime_mode
        app.system_cache["value"] = None
        app.system_cache["expires_at"] = 0
        gc.collect()
        self.tempdir.cleanup()

    def test_application_health_uses_container_safe_metrics(self):
        health = app.calculate_application_health()
        self.assertEqual(health["mode"], "application")
        self.assertTrue(health["database_healthy"])
        self.assertTrue(health["database_writable"])
        self.assertEqual(health["parser_backlog"], 2)
        self.assertIsNotNone(health["last_packet_seconds"])
        self.assertIn("parser", health["services"])

    def test_docker_page_uses_application_health_title(self):
        with app.app.test_request_context():
            page = app.render_template(
                "index.html",
                stats={"active_aircraft": 0},
                station={
                    "name": "Test station",
                    "latitude": 0,
                    "longitude": 0,
                    "timezone": "UTC",
                },
                runtime_mode="docker",
            )
        self.assertIn("Application health", page)
        self.assertNotIn("Receiver health", page)


if __name__ == "__main__":
    unittest.main()
