import urllib.error
import urllib.request

import pytest

import paseo_http


@pytest.mark.parametrize("code", [302, 303])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_credentialed_request_refuses_redirect_without_forwarding_auth(
    monkeypatch, code, method
):
    secret = "synthetic-bearer-secret"
    requests = []

    def open_request(self, request, timeout):
        requests.append((request.full_url, request.get_header("Authorization")))
        raise urllib.error.HTTPError(
            request.full_url,
            code,
            "redirect refused",
            {"Location": "https://external.invalid/"},
            None,
        )

    monkeypatch.setattr(paseo_http.urllib.request.OpenerDirector, "open", open_request)
    request = urllib.request.Request(
        "http://127.0.0.1/resource",
        data=b"payload" if method == "POST" else None,
        headers={"Authorization": "Bearer " + secret},
        method=method,
    )
    with pytest.raises(urllib.error.HTTPError, match="redirect refused") as error:
        paseo_http.open_credentialed(request, timeout=1)
    assert requests == [("http://127.0.0.1/resource", "Bearer " + secret)]
    assert secret not in str(error.value)
