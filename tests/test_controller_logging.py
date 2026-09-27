"""Controller diagnostics, using in-memory HTTP requests and no live connections."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import logging
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from tuoni_external.diagnostics import DiagnosticFormatter  # noqa: E402


spec = importlib.util.spec_from_file_location(
    "http_controller_logging_tests", ROOT / "examples/http-transport/controller.py",
)
controller_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller_module)


class MemorySocket:
    """The socket interface BaseHTTPRequestHandler uses, backed by BytesIO."""

    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, mode, buffering=None):
        if mode != "rb":
            raise AssertionError("unexpected socket file mode: %s" % mode)
        return self.input

    def sendall(self, data):
        self.output.write(data)


class ControllerLoggingTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(DiagnosticFormatter("json"))
        self.root_logger = logging.getLogger()
        self.old_handlers = self.root_logger.handlers[:]
        self.old_level = self.root_logger.level
        self.root_logger.handlers = [self.handler]
        self.root_logger.setLevel(logging.DEBUG)
        self.addCleanup(self.restore_logging)

        self.loggers = []
        for name in ("controller", "controller.http"):
            logger = logging.getLogger(name)
            self.loggers.append((logger, logger.level, logger.handlers[:],
                                 logger.propagate, logger.disabled))
            logger.handlers = []
            logger.setLevel(logging.NOTSET)
            logger.propagate = True
            logger.disabled = False

        self.listener_patch = mock.patch.object(controller_module, "ExternalListener", autospec=True)
        self.listener_class = self.listener_patch.start()
        self.addCleanup(self.listener_patch.stop)
        self.listener = self.listener_class.return_value
        self.old_controller = controller_module.controller
        self.addCleanup(setattr, controller_module, "controller", self.old_controller)
        self.controller = controller_module.Controller()
        controller_module.controller = self.controller

    def restore_logging(self):
        self.root_logger.handlers = self.old_handlers
        self.root_logger.setLevel(self.old_level)
        self.handler.close()
        for logger, level, handlers, propagate, disabled in self.loggers:
            logger.setLevel(level)
            logger.handlers = handlers
            logger.propagate = propagate
            logger.disabled = disabled

    def events(self, name=None):
        events = [json.loads(line) for line in self.stream.getvalue().splitlines()]
        return [event for event in events if name is None or event["event"] == name]

    def clear_events(self):
        self.stream.seek(0)
        self.stream.truncate(0)

    def request(self, data=None, *, body=None, path="/checkin", client="127.0.0.1",
                method="POST", headers=None, include_length=True):
        if body is None:
            body = json.dumps(data if data is not None else {"id": "agent-1", "type": "python"}).encode()
        lines = ["%s %s HTTP/1.1" % (method, path), "Host: fixture.invalid"]
        if include_length:
            lines.append("Content-Length: %d" % len(body))
        for key, value in (headers or {}).items():
            lines.append("%s: %s" % (key, value))
        sock = MemorySocket(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
        server = SimpleNamespace(server_name="fixture.invalid", server_port=0)
        handler = controller_module.AgentHandler(sock, (client, 12345), server)
        return handler, sock.output.getvalue(), body

    def test_success_keeps_response_and_logs_correlated_measurements(self):
        _, response, body = self.request(path="/checkin?token=query-secret")
        headers, payload = response.split(b"\r\n\r\n", 1)
        self.assertTrue(headers.startswith(b"HTTP/1.0 200 "))
        self.assertIn(b"Content-Type: application/json", headers)
        self.assertIn(b"Content-Length: 2", headers)
        self.assertNotIn(b"request_id", response)
        self.assertEqual(payload, b"{}")

        completion, = self.events("http_request_completed")
        registration, = self.events("agent_registration_submitted")
        self.assertEqual(completion["level"], "DEBUG")
        self.assertEqual(completion["method"], "POST")
        self.assertEqual(completion["path"], "/checkin")
        self.assertEqual(completion["status_code"], 200)
        self.assertEqual(completion["bytes_received"], len(body))
        self.assertEqual(completion["bytes_sent"], 2)
        self.assertGreaterEqual(completion["duration_ms"], 0)
        self.assertRegex(completion["request_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(registration["request_id"], completion["request_id"])
        self.assertRegex(completion["timestamp"], r"\.\d{3}Z$")
        self.assertNotIn("query-secret", self.stream.getvalue())
        self.listener.new_agent.assert_called_once_with("agent-1", metadata={})

    def test_invalid_json_logs_reason_without_body_or_headers(self):
        _, response, body = self.request(
            body=b'{"password": "body-secret",', path="/checkin?key=query-secret",
            headers={"Authorization": "Bearer header-secret"},
        )
        self.assertEqual(response.split(b"\r\n\r\n", 1)[1], b"{}")
        warning, = self.events("http_request_ignored")
        self.assertEqual(warning["level"], "WARNING")
        self.assertEqual(warning["reason"], "invalid_json")
        self.assertEqual(warning["bytes_received"], len(body))
        self.assertGreaterEqual(warning["json_line"], 1)
        self.assertGreaterEqual(warning["json_column"], 1)
        self.assertNotIn("exception", warning)
        for secret in ("body-secret", "query-secret", "header-secret", "Authorization"):
            self.assertNotIn(secret, self.stream.getvalue())
        self.listener.new_agent.assert_not_called()

    def test_missing_length_logs_reason_and_preserves_empty_response(self):
        _, response, _ = self.request(include_length=False)
        warning, = self.events("http_request_ignored")
        self.assertEqual(warning["reason"], "missing_content_length")
        self.assertEqual(response.split(b"\r\n\r\n", 1)[1], b"{}")

    def test_invalid_length_logs_reason_without_exposing_header_value(self):
        handler, response, _ = self.request(
            include_length=False, headers={"Content-Length": "private-invalid-length"},
        )
        warning, = self.events("http_request_ignored")
        self.assertEqual(warning["level"], "WARNING")
        self.assertEqual(warning["reason"], "invalid_content_length")
        self.assertNotIn("exception", warning)
        self.assertNotIn("private-invalid-length", self.stream.getvalue())
        self.assertTrue(handler.close_connection)
        self.assertEqual(response, b"")
        completion, = self.events("http_request_completed")
        self.assertEqual(completion["bytes_sent"], 0)

    def test_handler_exception_has_traceback_and_closes_connection(self):
        with mock.patch.object(self.controller, "add_agent", side_effect=RuntimeError("fixture failed")):
            handler, response, _ = self.request()
        failure, = self.events("http_request_failed")
        completion, = self.events("http_request_completed")
        self.assertEqual(failure["level"], "ERROR")
        self.assertIn("RuntimeError: fixture failed", failure["exception"])
        self.assertIn("_handle_post", failure["exception"])
        self.assertEqual(failure["request_id"], completion["request_id"])
        self.assertEqual(completion["level"], "ERROR")
        self.assertTrue(handler.close_connection)
        self.assertEqual(response, b"")

    def test_callback_exception_logs_context_and_traceback(self):
        self.controller._on_command("unregistered", "my_what", "command-1", {})
        failure, = self.events("command_callback_failed")
        self.assertEqual(failure["level"], "ERROR")
        self.assertEqual(failure["agent_guid"], "unregistered")
        self.assertEqual(failure["command_id"], "command-1")
        self.assertEqual(failure["template_name"], "my_what")
        self.assertIn("KeyError", failure["exception"])
        self.assertNotIn("request_id", failure)
        self.listener.new_result.assert_called_once()
        self.assertFalse(self.listener.new_result.call_args.args[2])

    def test_control_characters_are_escaped_and_query_is_excluded(self):
        self.request(path="/check\x1b[31m?token=query-secret", client="client\nforged")
        completion, = self.events("http_request_completed")
        self.assertNotIn("\x1b", completion["path"])
        self.assertIn(r"\x1b", completion["path"])
        self.assertNotIn("\n", completion["client"])
        self.assertIn(r"\n", completion["client"])
        self.assertNotIn("query-secret", self.stream.getvalue())

    def test_parallel_requests_keep_context_separate_and_reset_afterward(self):
        barrier = threading.Barrier(2)
        original = controller_module.AgentHandler._handle_post

        def overlapping_post(handler):
            barrier.wait(timeout=5)
            original(handler)

        def send(index):
            return self.request({"id": "agent-%d" % index, "type": "python"},
                                path="/request-%d" % index, client="client-%d" % index)

        with mock.patch.object(controller_module.AgentHandler, "_handle_post", overlapping_post):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(send, (1, 2)))
        self.assertEqual(len(results), 2)
        completions = self.events("http_request_completed")
        self.assertEqual(len({event["request_id"] for event in completions}), 2)
        for event in completions:
            index = event["path"].split("-")[-1]
            self.assertEqual(event["client"], "client-" + index)
            registration = next(item for item in self.events("agent_registration_submitted")
                                if item["agent_guid"] == "agent-" + index)
            self.assertEqual(registration["request_id"], event["request_id"])
        controller_module.logger.info("outside_request")
        outside, = self.events("outside_request")
        self.assertNotIn("request_id", outside)
        self.assertNotIn("client", outside)

    def test_info_level_hides_empty_polling_but_retains_warnings(self):
        self.request()
        self.clear_events()
        self.root_logger.setLevel(logging.INFO)
        self.request()
        self.assertEqual(self.events(), [])
        self.request(body=b"invalid fixture JSON")
        warning, = self.events()
        self.assertEqual(warning["event"], "http_request_ignored")
        self.assertEqual(warning["level"], "WARNING")

    def test_http_errors_have_warning_status_without_query_or_header_values(self):
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                self.clear_events()
                _, response, _ = self.request(method=method, path="/unsupported?token=query-secret",
                                              headers={"Authorization": "header-secret"})
                self.assertTrue(response.startswith(b"HTTP/1.0 501 "))
                completion, = self.events("http_request_completed")
                self.assertEqual(completion["level"], "WARNING")
                self.assertEqual(completion["status_code"], 501)
                self.assertEqual(completion["path"], "/unsupported")
                self.assertIsNone(completion["bytes_sent"])
                self.assertTrue(self.events("http_protocol_error"))
                self.assertNotIn("query-secret", self.stream.getvalue())
                self.assertNotIn("header-secret", self.stream.getvalue())

    def test_malformed_success_values_are_omitted_from_info_and_payload_previews(self):
        for value in ({"password": "private-secret"}, ["private-secret"], "private-secret"):
            for previews in (False, True):
                with self.subTest(value_type=type(value).__name__, previews=previews):
                    self.clear_events()
                    self.controller.log_payloads = previews
                    self.root_logger.setLevel(logging.DEBUG if previews else logging.INFO)
                    self.request({"id": "agent-1", "type": "python",
                                  "result": "private-output", "success": value})
                    result, = self.events("result_submitted")
                    self.assertEqual(result["level"], "INFO")
                    self.assertEqual(result["success"], "<invalid %s>" % type(value).__name__)
                    self.assertNotIn("private-secret", self.stream.getvalue())
                    self.assertNotIn("private-output", self.stream.getvalue())
                    payload_events = self.events("http_payload_received")
                    self.assertEqual(len(payload_events), 1 if previews else 0)
                    if previews:
                        preview = json.loads(payload_events[0]["payload_preview"])
                        self.assertEqual(preview["success"], "<invalid %s>" % type(value).__name__)

    def test_payload_previews_require_opt_in_and_redact_content(self):
        data = {"id": "agent-1", "type": "python", "username": "private-user",
                "configuration": {"password": "private-config"}, "result": "private-output"}
        self.request(data)
        self.assertEqual(self.events("http_payload_received"), [])
        self.assertNotIn("private-", self.stream.getvalue())
        self.clear_events()
        self.controller.log_payloads = True
        self.request(data)
        preview_event, = self.events("http_payload_received")
        preview = json.loads(preview_event["payload_preview"])
        self.assertEqual(preview["id"], "agent-1")
        for field in ("username", "configuration", "result"):
            self.assertEqual(preview[field], "<redacted>")
        self.assertNotIn("private-", self.stream.getvalue())
        self.assertIn("request_id", preview_event)

    def test_legacy_positionals_and_logging_options(self):
        args = controller_module.parse_args(["fixture.invalid", "8123"])
        self.assertEqual((args.ws_host, args.ws_port, args.http_bind, args.http_port),
                         ("fixture.invalid", 8123, "127.0.0.1", 8080))
        self.assertEqual((args.log_level, args.log_format, args.log_payloads), ("INFO", "text", False))
        args = controller_module.parse_args([
            "fixture.invalid", "8123", "0.0.0.0", "8081", "--log-level", "debug",
            "--log-format", "json", "--log-file", "fixture.log", "--log-max-bytes", "4096",
            "--log-backups", "2", "--log-payloads", "--log-payload-limit", "128",
        ])
        self.assertEqual((args.http_bind, args.http_port), ("0.0.0.0", 8081))
        self.assertEqual((args.log_level, args.log_format, args.log_file), ("DEBUG", "json", "fixture.log"))
        self.assertEqual((args.log_max_bytes, args.log_backups, args.log_payload_limit), (4096, 2, 128))
        self.assertTrue(args.log_payloads)

    def test_help_and_invalid_arguments_open_no_sockets(self):
        cases = [(["--help"], 0),
                 (["fixture.invalid", "8123", "--log-level", "invalid"], 2),
                 (["fixture.invalid", "8123", "--log-format", "xml"], 2)]
        cases.extend((["fixture.invalid", value], 2)
                     for value in ("0", "-1", "65536", "invalid"))
        cases.extend((["fixture.invalid", "8123", "127.0.0.1", value], 2)
                     for value in ("-1", "65536", "invalid"))
        for option in ("--log-max-bytes", "--log-backups", "--log-payload-limit"):
            cases.extend((["fixture.invalid", "8123", option, value], 2)
                         for value in ("0", "-1", "invalid"))
        with mock.patch.object(controller_module.HTTPServer, "__init__") as server_init:
            for argv, exit_code in cases:
                with self.subTest(argv=argv), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        controller_module.main(argv)
                    self.assertEqual(raised.exception.code, exit_code)
            server_init.assert_not_called()
        self.listener.connect.assert_not_called()

    def test_port_boundaries_preserve_ephemeral_http_bind(self):
        for remote_port in (1, 65535):
            for bind_port in (0, 65535):
                with self.subTest(remote_port=remote_port, bind_port=bind_port):
                    args = controller_module.parse_args([
                        "fixture.invalid", str(remote_port), "127.0.0.1", str(bind_port),
                    ])
                    self.assertEqual((args.ws_port, args.http_port), (remote_port, bind_port))

    def test_bind_failure_is_logged_without_connecting_or_announcing_listening(self):
        for error in (OSError("fixture address already in use"), OverflowError("port out of range")):
            with self.subTest(error=type(error).__name__):
                self.clear_events()
                self.listener_class.reset_mock()
                with mock.patch.object(controller_module, "configure_logging"), \
                        mock.patch.object(controller_module.HTTPServer, "__init__", side_effect=error):
                    status = controller_module.main(["fixture.invalid", "8123"])
                self.assertEqual(status, 1)
                failure, = self.events("http_bind_failed")
                self.assertEqual(failure["level"], "ERROR")
                self.assertIn(type(error).__name__, failure["exception"])
                self.assertEqual((failure["host"], failure["port"]), ("127.0.0.1", 8080))
                self.assertEqual(self.events("http_server_listening"), [])
                self.listener_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
