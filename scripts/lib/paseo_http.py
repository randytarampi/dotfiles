"""HTTP helpers for requests carrying credentials."""

import ipaddress
import urllib.parse
import urllib.request


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _is_loopback_request(request):
    """Select a direct transport for literal loopback hosts only."""
    try:
        host = urllib.parse.urlsplit(request.full_url).hostname
        if host == "localhost":
            return True
        return bool(host and ipaddress.ip_address(host).is_loopback)
    except (TypeError, ValueError):
        return False


def open_credentialed(request, timeout):
    """Refuse redirects and never proxy a loopback credential request."""
    handlers = [_RefuseRedirects()]
    if _is_loopback_request(request):
        # urllib's default ProxyHandler honours http_proxy even for loopback
        # when no_proxy is empty. Refuse that credential egress path.
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    return opener.open(request, timeout=timeout)
