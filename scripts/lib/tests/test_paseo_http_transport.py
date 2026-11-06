"""Exercise urllib credential transport using disposable loopback servers only."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import urllib.error
import urllib.request

import pytest

import paseo_http


@pytest.fixture
def launch_server():
    servers = []

    def launch(handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return server

    yield launch
    for server, thread in reversed(servers):
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _set_proxy(monkeypatch, port):
    for name in (
        "http_proxy",
        "HTTP_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{port}")
    # This test must fail if the implementation relies on NO_PROXY.
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")


def test_loopback_credentials_never_reach_inherited_proxy(monkeypatch, launch_server):
    proxy_hits = []
    local_hits = []

    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            proxy_hits.append((self.path, self.headers.get("Authorization")))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    class Local(BaseHTTPRequestHandler):
        def do_GET(self):
            local_hits.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"local-inventory")

        def log_message(self, *args):
            pass

    proxy = launch_server(Proxy)
    local = launch_server(Local)
    _set_proxy(monkeypatch, proxy.server_port)
    request = urllib.request.Request(
        f"http://127.0.0.1:{local.server_port}/v1/models",
        headers={"Authorization": "Bearer synthetic-test-only"},
    )
    with paseo_http.open_credentialed(request, timeout=2) as response:
        assert response.status == 200
        assert response.read() == b"local-inventory"
    assert local_hits == [("/v1/models", "Bearer synthetic-test-only")]
    assert proxy_hits == []


def test_remote_credential_request_preserves_configured_proxy(
    monkeypatch, launch_server
):
    requests = []

    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"proxied")

        def log_message(self, *args):
            pass

    proxy = launch_server(Proxy)
    _set_proxy(monkeypatch, proxy.server_port)
    request = urllib.request.Request(
        "http://external.invalid/v1/models",
        headers={"Authorization": "Bearer synthetic-test-only"},
    )
    with paseo_http.open_credentialed(request, timeout=2) as response:
        assert response.status == 200
        assert response.read() == b"proxied"
    assert requests == [
        ("http://external.invalid/v1/models", "Bearer synthetic-test-only")
    ]


@pytest.mark.parametrize("status", [302, 303])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_redirect_does_not_contact_sink(launch_server, monkeypatch, status, method):
    source_hits = []
    sink_hits = []

    class Sink(BaseHTTPRequestHandler):
        def do_GET(self):
            sink_hits.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *args):
            pass

    sink = launch_server(Sink)

    class Source(BaseHTTPRequestHandler):
        def do_GET(self):
            source_hits.append(self.headers.get("Authorization"))
            self.send_response(status)
            self.send_header("Location", f"http://127.0.0.1:{sink.server_port}/sink")
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *args):
            pass

    source = launch_server(Source)
    for name in ("http_proxy", "HTTP_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    request = urllib.request.Request(
        f"http://127.0.0.1:{source.server_port}/source",
        data=b"payload" if method == "POST" else None,
        headers={"Authorization": "Bearer synthetic-test-only"},
        method=method,
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        paseo_http.open_credentialed(request, timeout=2)
    assert error.value.code == status
    assert source_hits == ["Bearer synthetic-test-only"]
    assert sink_hits == []
