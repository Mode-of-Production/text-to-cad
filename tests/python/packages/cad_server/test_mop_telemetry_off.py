"""MoP fork (061 UV40): nothing in cad_server can send telemetry or read the version feed.

The six calls run with every socket connect blocked and with the environment asking for telemetry
(CAD_TELEMETRY=1, no DO_NOT_TRACK, no CI). No connect may be attempted."""

from __future__ import annotations

import os
import socket
import unittest
from unittest import mock

from cad_server import analytics, updates


class MopTelemetryOffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.attempts: list[str] = []

        def blocked(name):
            def call(*args, **kwargs):
                self.attempts.append(name)
                raise OSError(f"MoP test: {name} blocked")
            return call

        for patcher in (
            mock.patch.object(socket.socket, "connect", blocked("connect")),
            mock.patch.object(socket.socket, "connect_ex", blocked("connect_ex")),
            mock.patch.object(socket, "create_connection", blocked("create_connection")),
            mock.patch.dict(os.environ, {"CAD_TELEMETRY": "1", "CAD_UPDATE_CHECK": "1"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("DO_NOT_TRACK", "CI"):
            os.environ.pop(name, None)

    def test_mop_nothing_can_send_with_sockets_blocked(self) -> None:
        self.assertIs(analytics._environment(), False)
        self.assertIs(analytics._environment({"CAD_TELEMETRY": "1"}), False)
        self.assertEqual(analytics._post("https://example.invalid/telemetry", {"a": 1}), "refused")
        self.assertIs(updates.checking(), False)
        self.assertIs(updates.checking(asked=True), False)
        self.assertIsNone(updates._get("https://example.invalid/feed"))
        self.assertEqual(self.attempts, [])


if __name__ == "__main__":
    unittest.main()
