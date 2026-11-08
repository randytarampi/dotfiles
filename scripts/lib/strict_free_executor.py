"""Pure test adapter for account-qualified dispatch; NOT connected to OpenCode.

Only a real, trusted pre-request integration can enforce this contract.
"""

from strict_free_admission import admit, admit_chain


class StrictFreeDenied(RuntimeError):
    """Reject an unqualified request without attempting another backend."""


def guarded_request(route, evidence, *, now, tokens, privacy_classes, send):
    """Authorize one selected backend immediately before a supplied test sender."""
    decision = admit(
        route, evidence, now=now, tokens=tokens, privacy_classes=privacy_classes
    )
    if not decision.allowed:
        raise StrictFreeDenied(decision.reason)
    return send(route)


def guarded_choice(
    routes, evidence, *, selected_index, now, tokens, privacy_classes, send
):
    """Reject *any* unqualified fallback before sending to the selected backend.

    This is deliberately not a fallback manager. A 429 or other send failure
    propagates to the caller; it cannot silently reuse pre-429 quota evidence.
    """
    decision = admit_chain(
        routes, evidence, now=now, tokens=tokens, privacy_classes=privacy_classes
    )
    if not decision.allowed:
        raise StrictFreeDenied(decision.reason)
    if (
        not isinstance(selected_index, int)
        or isinstance(selected_index, bool)
        or not 0 <= selected_index < len(routes)
    ):
        raise StrictFreeDenied("invalid-backend-selection")
    return send(routes[selected_index])
