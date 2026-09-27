"""Bridge entry-point diagnostics, with no RPC or WebSocket connections."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import logging
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from tuoni_external import ExternalListener  # noqa: E402


spec = importlib.util.spec_from_file_location(
    "bridge_logging_tests", ROOT / "examples/metasploit-bridge/proxy_metasploit.py",
)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeLoggingTests(unittest.TestCase):
    def test_os_metadata_distinguishes_darwin_from_windows(self):
        for name, expected in (("Darwin", "MAC"), ("Windows 11", "WINDOWS"),
                               ("macOS", "MAC"), ("Ubuntu Linux", "LINUX"),
                               ("FreeBSD", "BSD"), ("unknown", None)):
            with self.subTest(name=name):
                self.assertEqual(bridge.MetasploitProxy._normalize_os(name), expected)

    def test_startup_failure_is_logged_and_returns_failure_status(self):
        with mock.patch.object(bridge, "configure_logging"), \
                mock.patch.object(bridge, "MetasploitProxy", autospec=True) as proxy, \
                mock.patch.object(bridge.time, "sleep") as sleep, \
                self.assertLogs("metasploit_bridge", level="ERROR") as logs:
            proxy.return_value.connect.side_effect = ValueError("fixture invalid configuration")
            self.assertEqual(bridge.main(), 1)
        sleep.assert_not_called()
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].getMessage(), "bridge_failed")
        self.assertIs(logs.records[0].exc_info[0], ValueError)

    def test_entry_point_retains_library_error_and_close_details(self):
        root = logging.getLogger()
        previous_handlers, previous_level = root.handlers, root.level
        root.handlers = []  # Keep configure_logging from closing application handlers.
        listener = ExternalListener()

        def connection_events(*args):
            listener._on_message(None, '{"type":"error","error":"fixture rejection"}')
            listener._on_close(None, 1008, "fixture close reason")

        try:
            with redirect_stderr(io.StringIO()) as stderr, \
                    redirect_stdout(io.StringIO()) as stdout, \
                    mock.patch.object(bridge, "MetasploitProxy", autospec=True) as proxy, \
                    mock.patch.object(bridge.time, "sleep", side_effect=KeyboardInterrupt):
                proxy.return_value.connect.side_effect = connection_events
                self.assertEqual(bridge.main(), 0)
            output = stderr.getvalue()
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn('error="fixture rejection"', output)
            self.assertIn("close_code=1008", output)
            self.assertIn('close_reason="fixture close reason"', output)
            self.assertIn("bridge_running", output)
            self.assertIn("bridge_stopping", output)
            self.assertEqual(len(output.splitlines()), 4)
            proxy.return_value.connect.assert_called_once_with(
                bridge.TUONI_LISTENER["hostname"], bridge.TUONI_LISTENER["port"],
            )
        finally:
            for handler in root.handlers:
                handler.close()
            root.handlers = previous_handlers
            root.setLevel(previous_level)


if __name__ == "__main__":
    unittest.main()
