import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import opencode_web


class _Server:
    def __init__(
        self,
        body=b'{"version":"2.2.5"}',
        content_type="application/json",
        status=200,
        delay=0.0,
    ):
        self.body = body
        self.content_type = content_type
        self.status = status
        self.delay = delay
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/api/info":
                    self.send_error(404)
                    return
                if (
                    self.headers.get("Authorization")
                    != "Basic " + base64.b64encode(b"opencode:secret").decode()
                ):
                    self.send_error(401)
                    return
                if owner.delay:
                    import time

                    time.sleep(owner.delay)
                self.send_response(owner.status)
                self.send_header("Content-Type", owner.content_type)
                self.end_headers()
                self.wfile.write(owner.body)

            def log_message(self, format: str, *args: Any) -> None:
                # Intentional override: keep probe traffic out of pytest logs.
                pass

        self.http = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return "http://127.0.0.1:{}".format(self.http.server_port)

    def __exit__(self, *_args):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()


def test_json_ok_and_version_parsing():
    with _Server() as url:
        result, info = opencode_web.request_server_info(url, "secret")
    assert result == "ok"
    assert isinstance(info, dict)
    assert info["version"] == "2.2.5"


def test_html_false_positive_is_rejected():
    with _Server(b"<html>SPA</html>", "text/html") as url:
        assert opencode_web.request_server_info(url, "secret") == ("fail", "non-json")


def test_unauthorized_is_rejected():
    with _Server() as url:
        assert opencode_web.request_server_info(url, "wrong")[0] == "fail"


def test_timeout_is_rejected():
    with _Server(delay=0.2) as url:
        assert opencode_web.request_server_info(url, "secret", timeout=0.01) == (
            "fail",
            "request-failed",
        )


def test_version_mismatch_is_reported():
    with _Server(json.dumps({"version": "2.2.5"}).encode()) as url:
        assert opencode_web.request_server_info(
            url, "secret", expected_version="2.2.4"
        ) == ("fail", "version-mismatch:2.2.5")
