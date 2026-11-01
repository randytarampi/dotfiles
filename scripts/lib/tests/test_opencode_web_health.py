import base64
import json
import runpy
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

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


def test_cli_env_mode_and_port_validation(monkeypatch):
    """__main__ honours the env password chain (secrets never in argv),
    exits 2 on unknown (no credential) and 1 on an invalid port."""
    module_argv = [str(opencode_web.__file__), "4096"]
    monkeypatch.delenv("OPENCODE_PASSWORD", raising=False)
    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD", raising=False)
    monkeypatch.setattr(opencode_web.sys, "argv", module_argv)
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("opencode_web", run_name="__main__", alter_sys=True)
    assert excinfo.value.code == 2

    monkeypatch.setenv("OPENCODE_PASSWORD", "secret")
    monkeypatch.setattr(
        opencode_web.sys, "argv", [str(opencode_web.__file__), "4096@attacker"]
    )
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("opencode_web", run_name="__main__", alter_sys=True)
    assert excinfo.value.code == 1

    monkeypatch.setattr(
        opencode_web.sys, "argv", [str(opencode_web.__file__), "not-a-port"]
    )
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("opencode_web", run_name="__main__", alter_sys=True)
    assert excinfo.value.code == 1

    monkeypatch.setattr(
        opencode_web.sys,
        "argv",
        [str(opencode_web.__file__), "4096", "secret-in-argv"],
    )
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("opencode_web", run_name="__main__", alter_sys=True)
    assert excinfo.value.code == 1

    monkeypatch.setattr(
        opencode_web.sys,
        "argv",
        [str(opencode_web.__file__), "4096", "secret-in-argv"],
    )
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("opencode_web", run_name="__main__", alter_sys=True)
    assert excinfo.value.code == 1

    # Success case: real probe against the local stub server, secrets via
    # env only (argv stays password-free).
    with _Server() as stub_url:
        port = stub_url.rsplit(":", 1)[1]
        subprocess_result = __import__("subprocess").run(
            [__import__("sys").executable, str(opencode_web.__file__), port],
            env={
                **__import__("os").environ,
                "PYTHONPATH": str(opencode_web.__file__).rsplit("/scripts/lib/", 1)[0]
                + "/scripts/lib",
                "OPENCODE_PASSWORD": "secret",
            },
            capture_output=True,
            text=True,
            check=False,
        )
    assert subprocess_result.returncode == 0, subprocess_result.stderr


@pytest.mark.parametrize(
    "value", ["4096@attacker", "0", "65536", " 4096", "4096\n", "٤٠٩٦"]
)
def test_strict_port_parser(value):
    assert opencode_web._validated_port(value) is None


def test_valid_port_bounds():
    assert opencode_web._validated_port("1") == 1
    assert opencode_web._validated_port("65535") == 65535
    assert opencode_web._validated_port("01") is None
