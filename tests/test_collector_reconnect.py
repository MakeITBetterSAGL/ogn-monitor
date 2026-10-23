import gc
import socket
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import app
import collector


class StopCollection(BaseException):
    """Stop the production loop after the scripted connections are exhausted."""


class CollectorReconnectTest(unittest.TestCase):
    def test_dashboard_recovers_after_receiver_restart_without_traffic(self):
        with ExitStack() as stack:
            temporary = stack.enter_context(tempfile.TemporaryDirectory())
            stack.callback(gc.collect)
            database_path = Path(temporary) / "ogn.sqlite3"
            stack.enter_context(patch.object(collector, "DATABASE", database_path))
            database = collector.open_database()
            database.close()
            stack.enter_context(patch.object(app, "DATABASE", database_path))
            stack.enter_context(patch.object(app, "DECODER_HOST", "127.0.0.1"))
            stack.enter_context(patch.dict(app.stats_cache, value=None, expires_at=0))
            stack.enter_context(patch.object(app, "STATS_CACHE_SECONDS", 0))
            listener = stack.enter_context(socket.socket())
            listener.bind(("127.0.0.1", 0))
            address = listener.getsockname()
            stack.enter_context(patch.object(app, "DECODER_PORT", address[1]))
            client = app.app.test_client()
            # A bound socket without listen represents the powered-off decoder.
            self.assertFalse(client.get("/api/stats").get_json()["receiver_online"])
            listener.listen()
            self.assertTrue(client.get("/api/stats").get_json()["receiver_online"])
            listener.close()
            self.assertFalse(client.get("/api/stats").get_json()["receiver_online"])
            restarted = stack.enter_context(socket.socket())
            restarted.bind(address)
            restarted.listen()
            stats = client.get("/api/stats").get_json()
            self.assertTrue(stats["receiver_online"])
            self.assertFalse(stats["active_traffic"])
            self.assertEqual(stats["total_packets"], 0)
            for endpoint in ("/", "/api/aircraft", "/api/history/today", "/api/tracks"):
                with self.subTest(endpoint=endpoint):
                    self.assertEqual(client.get(endpoint).status_code, 200)

    def check_recovery(self, failure):
        with ExitStack() as stack:
            temporary = stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(patch.object(
                collector, "DATABASE", Path(temporary) / "ogn.sqlite3"
            ))
            database = collector.open_database()
            stack.callback(database.close)
            first, old_decoder = socket.socketpair()
            second, new_decoder = socket.socketpair()
            for sock in (first, old_decoder, second, new_decoder):
                stack.callback(sock.close)
            before = b"APRS <- FLR123456>OGFLR:before shutdown\n"
            after = b"APRS <- FLR654321>OGFLR:after restart\n"
            old_decoder.sendall(before)
            if failure == "eof":
                old_decoder.shutdown(socket.SHUT_WR)
            elif failure == "partial":
                old_decoder.sendall(b"APRS <- FLR999999>OGFLR:incomplete")
            # For timeout cases the old peer stays open but stops sending.
            new_decoder.sendall(b"unrelated log message\n")
            new_decoder.sendall(b"APRS <- STATION>OGNSDR:beacon\n")
            new_decoder.sendall(after)
            new_decoder.shutdown(socket.SHUT_WR)
            connect = stack.enter_context(patch.object(
                collector.socket, "create_connection",
                side_effect=[first, ConnectionRefusedError("station off"),
                             second, StopCollection()],
            ))
            sleep = stack.enter_context(patch.object(collector.time, "sleep"))
            stack.enter_context(patch.object(collector, "open_database", return_value=database))
            # Use real socket read timeouts, shortened only for the test.
            stack.enter_context(patch.object(collector, "DECODER_READ_TIMEOUT_SECONDS", 0.05))
            with self.assertRaises(StopCollection):
                collector.collect()
            rows = database.execute(
                "SELECT sender, destination, raw_packet FROM packets ORDER BY id"
            ).fetchall()
            self.assertEqual(rows, [
                ("FLR123456", "OGFLR", before.decode().strip()),
                ("FLR654321", "OGFLR", after.decode().strip()),
            ])
            self.assertEqual(connect.call_count, 4)
            self.assertEqual([call.args for call in sleep.call_args_list], [(5,), (5,), (5,)])
            self.assertEqual(first.fileno(), -1)
            self.assertEqual(second.fileno(), -1)

    def test_recovers_after_orderly_shutdown_and_connection_refusal(self):
        self.check_recovery("eof")

    def test_recovers_when_old_connection_remains_silent(self):
        self.check_recovery("silent")

    def test_discards_stalled_partial_line_and_recovers(self):
        self.check_recovery("partial")


if __name__ == "__main__":
    unittest.main()
