"""Offline checks for diagnostic formatting and WebSocket log integration."""

from contextlib import contextmanager, redirect_stderr
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import io
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import tuoni_external
from tuoni_external.diagnostics import (
    DiagnosticFormatter,
    clean_text,
    configure_logging,
    log_context,
    log_payload,
)


@contextmanager
def capture_logs(name="tuoni.tests.diagnostics", level=logging.DEBUG, output_format="json"):
    """Capture a named logger while preserving any application configuration."""
    logger = logging.getLogger(name)
    previous = (logger.handlers, logger.level, logger.propagate, logger.disabled)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(DiagnosticFormatter(output_format))
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
    logger.disabled = False
    try:
        yield logger, output
    finally:
        logger.handlers, logger.level, logger.propagate, logger.disabled = previous
        handler.close()


def read_events(output):
    return [json.loads(line) for line in output.getvalue().splitlines()]


class FormatterTests(unittest.TestCase):
    def test_timestamp_level_logger_thread_and_context(self):
        record = logging.LogRecord("component", logging.WARNING, __file__, 1,
                                   "request_%s", ("completed",), None)
        record.created = datetime(2026, 9, 26, 12, 34, 56, 123000,
                                  tzinfo=timezone.utc).timestamp()
        record.threadName = "worker-one"
        record.status = 400
        with log_context(request_id="request-1", duration_ms=1.25):
            event = json.loads(DiagnosticFormatter("json").format(record))
            line = DiagnosticFormatter("text").format(record)
        self.assertEqual(event, {
            "timestamp": "2026-09-26T12:34:56.123Z",
            "level": "WARNING", "logger": "component", "thread": "worker-one",
            "event": "request_completed", "request_id": "request-1",
            "duration_ms": 1.25, "status": 400,
        })
        self.assertTrue(line.startswith("2026-09-26T12:34:56.123Z WARNING"))
        self.assertIn("component [worker-one] request_completed", line)
        self.assertIn('request_id="request-1"', line)
        self.assertIn("duration_ms=1.25", line)
        self.assertIn("status=400", line)
        self.assertEqual(len(line.splitlines()), 1)

    def test_exception_preserves_traceback_without_extra_log_lines(self):
        for output_format in ("json", "text"):
            with self.subTest(output_format=output_format):
                with capture_logs(output_format=output_format) as (logger, output):
                    try:
                        raise ValueError("bad\nvalue\x1b[31m")
                    except ValueError:
                        logger.exception("request_failed\r\nforged_event")
                lines = output.getvalue().splitlines()
                self.assertEqual(len(lines), 1)
                self.assertNotIn("\x1b", lines[0])
                self.assertIn("Traceback", lines[0])
                self.assertIn("ValueError", lines[0])
                self.assertIn("test_exception_preserves_traceback", lines[0])
                if output_format == "json":
                    event = json.loads(lines[0])
                    self.assertIn("\\n", event["exception"])
                    self.assertNotIn("\n", event["exception"])

    def test_controls_in_logger_thread_context_and_message_are_escaped(self):
        record = logging.LogRecord("component\ninjected\x1b", logging.INFO,
                                   __file__, 1, "event\r\nnext", (), None)
        record.threadName = "worker\nnext"
        record.detail = "value\t\x00\x1b"
        for output_format in ("text", "json"):
            with self.subTest(output_format=output_format):
                line = DiagnosticFormatter(output_format).format(record)
                self.assertEqual(len(line.splitlines()), 1)
                self.assertTrue(all(character.isprintable() for character in line))
                if output_format == "json":
                    self.assertTrue(all(character.isprintable()
                                        for character in json.loads(line)["logger"]))

    def test_context_nests_resets_after_exception_and_extra_overrides(self):
        with capture_logs() as (logger, output):
            with log_context(request_id="outer", component="http"):
                logger.info("outer")
                with self.assertRaises(ValueError):
                    with log_context(request_id="inner"):
                        logger.info("inner", extra={"component": "handler"})
                        raise ValueError("leave inner scope")
                logger.info("restored")
            logger.info("outside")
        events = read_events(output)
        self.assertEqual(events[0]["request_id"], "outer")
        self.assertEqual(events[1]["request_id"], "inner")
        self.assertEqual(events[1]["component"], "handler")
        self.assertEqual(events[2]["request_id"], "outer")
        self.assertNotIn("request_id", events[3])
        self.assertNotIn("component", events[3])

    def test_context_is_isolated_between_concurrent_threads(self):
        barrier = threading.Barrier(3, timeout=5)
        with capture_logs() as (logger, output):
            def worker(request_id):
                with log_context(request_id=request_id):
                    barrier.wait()
                    logger.info("thread_event")
                    barrier.wait()

            threads = [threading.Thread(target=worker, args=(name,), name=name)
                       for name in ("alpha", "beta")]
            with log_context(request_id="main"):
                for thread in threads:
                    thread.start()
                try:
                    barrier.wait()
                    logger.info("main_event")
                    barrier.wait()
                finally:
                    for thread in threads:
                        thread.join(timeout=5)
                self.assertFalse(any(thread.is_alive() for thread in threads))
            logger.info("after_context")
        events = read_events(output)
        worker_events = {event["thread"]: event["request_id"]
                         for event in events if event["event"] == "thread_event"}
        self.assertEqual(worker_events, {"alpha": "alpha", "beta": "beta"})
        self.assertEqual(next(event for event in events
                              if event["event"] == "main_event")["request_id"], "main")
        self.assertNotIn("request_id", events[-1])

    def test_json_fields_preserve_types_and_nonfinite_values_are_json_safe(self):
        with capture_logs() as (logger, output):
            logger.info("fields", extra={"count": 3, "enabled": True, "missing": None,
                                         "duration": float("inf"), "ratio": float("nan")})
        event = read_events(output)[0]
        self.assertEqual(event["count"], 3)
        self.assertIs(event["enabled"], True)
        self.assertIsNone(event["missing"])
        self.assertEqual(event["duration"], "inf")
        self.assertEqual(event["ratio"], "nan")

    def test_clean_text_is_bounded_and_escapes_terminal_controls(self):
        for value in ("x" * 2000, "\n\r\t\x1b\x00" * 200, "unicode: café"):
            for limit in (0, 1, 16, 128):
                with self.subTest(limit=limit, value=value[:20]):
                    result = clean_text(value, limit)
                    self.assertLessEqual(len(result), limit)
                    self.assertTrue(all(character.isprintable() for character in result))
        self.assertEqual(clean_text("café"), "café")


class PayloadPreviewTests(unittest.TestCase):
    def test_payload_preview_requires_opt_in_and_debug_level(self):
        payload = {"type": "example", "password": "never-print-this"}
        with capture_logs() as (logger, output):
            log_payload(logger, "payload", payload)
            self.assertEqual(output.getvalue(), "")
            logger.setLevel(logging.INFO)
            log_payload(logger, "payload", payload, enabled=True)
            self.assertEqual(output.getvalue(), "")
            logger.setLevel(logging.DEBUG)
            log_payload(logger, "payload", payload, enabled=True)
        event = read_events(output)[0]
        self.assertEqual(event["level"], "DEBUG")
        self.assertNotIn("never-print-this", output.getvalue())

    def test_sensitive_and_unknown_values_are_hidden_without_mutating_payload(self):
        payload = {
            "type": "start-command", "agentGuid": "agent-example",
            "commandId": "command-example", "success": True,
            "password": "secret-password", "token": "secret-token",
            "configuration": {"nested": {"value": "secret-config"}},
            "results": [{"value": "secret-result"}],
            "agentMetadata": {"username": "secret-user"},
            "unknown": {"value": ["secret-unknown"]},
            "errorMessage": "secret-error",
            "status": {"unexpected_nested": "secret-status"},
        }
        original = deepcopy(payload)
        with capture_logs() as (logger, output):
            log_payload(logger, "payload", payload, enabled=True)
        event = read_events(output)[0]
        preview = json.loads(event["payload_preview"])
        self.assertEqual(preview["type"], "start-command")
        self.assertEqual(preview["agentGuid"], "agent-example")
        self.assertIs(preview["success"], True)
        for key in ("password", "token", "configuration", "results", "agentMetadata",
                    "unknown", "errorMessage", "status"):
            self.assertEqual(preview[key], "<redacted>")
        self.assertNotIn("secret-", output.getvalue())
        self.assertEqual(payload, original)

    def test_non_object_payloads_are_omitted(self):
        for payload in (["secret-list"], "secret-string", b"secret-bytes", None):
            with self.subTest(payload_type=type(payload).__name__):
                with capture_logs() as (logger, output):
                    log_payload(logger, "payload", payload, enabled=True)
                self.assertNotIn("secret-", output.getvalue())
                self.assertIn("omitted", read_events(output)[0]["payload_preview"])

    def test_preview_bounds_and_field_count_limit(self):
        payload = {"type": "x" * 5000}
        payload.update({"field-%d" % index: "secret-value" for index in range(100)})
        for limit in (0, 1, 16, 128, 1024):
            with self.subTest(limit=limit):
                with capture_logs() as (logger, output):
                    log_payload(logger, "payload", payload, enabled=True, limit=limit)
                preview = read_events(output)[0]["payload_preview"]
                self.assertLessEqual(len(preview), limit)
                self.assertNotIn("secret-value", preview)
        with capture_logs() as (logger, output):
            log_payload(logger, "payload", payload, enabled=True, limit=2048)
        preview = json.loads(read_events(output)[0]["payload_preview"])
        self.assertIn("additional fields omitted", preview["..."])
        self.assertNotIn("field-99", preview)

    def test_nonfinite_envelope_values_do_not_raise(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with capture_logs() as (logger, output):
                    log_payload(logger, "payload", {"type": "example", "id": value},
                                enabled=True)
                self.assertEqual(len(read_events(output)), 1)


class WebSocketLoggingTests(unittest.TestCase):
    def test_invalid_ports_fail_before_creating_a_socket_or_thread(self):
        for port in (0, -1, 65536, True, "8123", 8123.5, None):
            with self.subTest(port=port):
                listener = tuoni_external.ExternalListener()
                with mock.patch.object(tuoni_external.websocket, "WebSocketApp") as app, \
                        mock.patch.object(tuoni_external.threading, "Thread") as thread:
                    with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
                        listener.connect("fixture.invalid", port)
                    app.assert_not_called()
                    thread.assert_not_called()

    def test_worker_setup_failure_stays_in_structured_logging(self):
        listener = tuoni_external.ExternalListener()
        with mock.patch.object(tuoni_external.websocket, "WebSocketApp", autospec=True) as app, \
                mock.patch.object(threading, "excepthook") as unhandled, \
                capture_logs("tuoni_external.websocket") as (_, output), \
                redirect_stderr(io.StringIO()) as raw_stderr:
            app.return_value.run_forever.side_effect = ValueError("fixture setup failed")
            listener.connect("fixture.invalid", 8123)
            listener._thread.join(timeout=5)
            self.assertFalse(listener._thread.is_alive())
            app.return_value.run_forever.assert_called_once_with(reconnect=5)
            unhandled.assert_not_called()
        self.assertEqual(raw_stderr.getvalue(), "")
        events = read_events(output)
        self.assertEqual([event["event"] for event in events],
                         ["websocket_connecting", "websocket_error"])
        self.assertEqual(events[1]["error_type"], "ValueError")
        self.assertIn("fixture setup failed", events[1]["exception"])
        self.assertEqual(events[1]["thread"], "tuoni-websocket")

    def test_import_and_instantiation_leave_root_logging_unchanged(self):
        root = logging.getLogger()
        before = (list(root.handlers), root.level, list(root.filters), root.disabled)
        importlib.reload(tuoni_external)
        tuoni_external.ExternalListener(verbose=False)
        tuoni_external.ExternalListener(verbose=True, log_payloads=True)
        after = (list(root.handlers), root.level, list(root.filters), root.disabled)
        self.assertEqual(after, before)

    def test_errors_and_connection_events_are_visible_with_verbose_disabled(self):
        listener = tuoni_external.ExternalListener(verbose=False)
        connected = mock.Mock()
        listener._on_connect = connected
        with capture_logs("tuoni_external.websocket", level=logging.INFO) as (_, output):
            listener._on_open(None)
            try:
                raise RuntimeError("socket failure")
            except RuntimeError as error:
                listener._on_error(None, error)
            listener._on_close(None, 1006, "connection lost\nextra")
            listener._on_message(None, json.dumps({"type": "error", "error": "request rejected"}))
        connected.assert_called_once_with()
        events = read_events(output)
        self.assertEqual([(event["event"], event["level"]) for event in events], [
            ("websocket_connected", "INFO"), ("websocket_error", "ERROR"),
            ("websocket_closed", "WARNING"), ("websocket_server_error", "ERROR"),
        ])
        self.assertIn("Traceback", events[1]["exception"])
        self.assertIn("RuntimeError", events[1]["exception"])
        self.assertEqual(events[2]["close_code"], 1006)
        self.assertEqual(events[2]["close_reason"], "connection lost\\nextra")
        self.assertEqual(events[3]["error"], "request rejected")

    def test_outgoing_wire_payload_is_unchanged_and_default_logs_exclude_body(self):
        listener = tuoni_external.ExternalListener()
        listener._ws = mock.Mock()
        payload = {"type": "send-command-result", "agentGuid": "agent-1",
                   "commandId": "command-1", "results": [{"value": "private café output"}]}
        original = deepcopy(payload)
        with capture_logs("tuoni_external.websocket") as (_, output):
            listener._send(payload)
        expected = json.dumps(original)
        listener._ws.send.assert_called_once_with(expected)
        self.assertEqual(payload, original)
        events = read_events(output)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "websocket_frame")
        self.assertEqual(events[0]["direction"], "outgoing")
        self.assertEqual(events[0]["bytes"], len(expected.encode("utf-8")))
        self.assertEqual(events[0]["command_id"], "command-1")
        self.assertNotIn("private", output.getvalue())
        self.assertNotIn("payload_preview", events[0])

    def test_incoming_dispatch_arguments_are_unchanged_with_preview_enabled(self):
        listener = tuoni_external.ExternalListener(log_payloads=True)
        listener._on_command = mock.Mock()
        payload = {"type": "start-command", "agentGuid": "agent-1", "templateName": "example",
                   "commandId": "command-1", "configuration": {"value": "secret-config"}}
        with capture_logs("tuoni_external.websocket") as (_, output):
            listener._on_message(None, json.dumps(payload))
        listener._on_command.assert_called_once_with(
            "agent-1", "example", "command-1", payload["configuration"],
        )
        events = read_events(output)
        self.assertEqual([event["event"] for event in events],
                         ["websocket_frame", "websocket_payload"])
        self.assertEqual(events[0]["direction"], "incoming")
        self.assertEqual(events[0]["template_name"], "example")
        self.assertNotIn("secret-config", output.getvalue())
        self.assertIn("<redacted>", events[1]["payload_preview"])

    def test_verbose_alias_enables_only_redacted_debug_previews(self):
        listener = tuoni_external.ExternalListener(verbose=True, payload_limit=100)
        listener._ws = mock.Mock()
        with capture_logs("tuoni_external.websocket") as (logger, output):
            listener._send({"type": "example", "configuration": {"token": "secret-token"}})
            events = read_events(output)
            self.assertEqual(len(events), 2)
            self.assertLessEqual(len(events[1]["payload_preview"]), 100)
            self.assertNotIn("secret-token", output.getvalue())
            output.seek(0)
            output.truncate(0)
            logger.setLevel(logging.INFO)
            listener._send({"type": "example"})
            self.assertEqual(output.getvalue(), "")


class LoggingConfigurationTests(unittest.TestCase):
    def test_rotating_utf8_file_output_and_retention(self):
        root = logging.getLogger()
        previous_handlers, previous_level = root.handlers, root.level
        # Detach existing application handlers so basicConfig(force=True) cannot close them.
        root.handlers = []
        try:
            with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()) as console:
                path = Path(directory) / "nested" / "diagnostics.log"
                configure_logging("INFO", "json", path, max_bytes=600, backup_count=2)
                for index in range(30):
                    root.info("retention_marker_%02d café", index)
                for handler in root.handlers:
                    handler.flush()
                files = sorted(path.parent.glob("diagnostics.log*"))
                self.assertEqual({file.name for file in files},
                                 {"diagnostics.log", "diagnostics.log.1", "diagnostics.log.2"})
                events = [json.loads(line) for file in files
                          for line in file.read_text(encoding="utf-8").splitlines()]
                self.assertTrue(all(event["level"] == "INFO" for event in events))
                self.assertIn("retention_marker_29 café", [event["event"] for event in events])
                self.assertNotIn("retention_marker_00 café", [event["event"] for event in events])
                self.assertEqual(len(read_events(console)), 30)
                for handler in root.handlers:
                    handler.close()
                root.handlers = []
        finally:
            for handler in root.handlers:
                handler.close()
            root.handlers = previous_handlers
            root.setLevel(previous_level)

    def test_invalid_format_and_rotation_limits_are_rejected(self):
        with self.assertRaises(ValueError):
            DiagnosticFormatter("invalid")
        for settings in ({"max_bytes": 0}, {"max_bytes": -1},
                         {"backup_count": 0}, {"backup_count": -1}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                configure_logging(**settings)


if __name__ == "__main__":
    unittest.main()
