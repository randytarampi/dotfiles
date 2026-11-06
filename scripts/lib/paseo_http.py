"""HTTP helpers for requests carrying credentials."""

import urllib.request


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_credentialed(request, timeout):
    """Open a credential-bearing request without following redirects."""
    opener = urllib.request.build_opener(_RefuseRedirects())
    return opener.open(request, timeout=timeout)
