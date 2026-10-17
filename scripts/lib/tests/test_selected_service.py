import pytest

from selected_service import (
    DIRECT_EXCEPTIONS,
    CanonicalSelection,
    Operation,
    Qualification as Q,
    RouteResolution,
    ServiceLocator,
    Transport,
    assert_renders_from,
    render_invariant_test,
    resolve_transport,
    select,
)


@pytest.mark.parametrize("credential", [True, False])
@pytest.mark.parametrize(
    "q", [Q.PROTOCOL_PROBED, Q.DISCOVERED, Q.UNKNOWN, Q.UNAVAILABLE]
)
@pytest.mark.parametrize("gate", ["direct", "gateway"])
def test_route_matrix(gate, q, credential):
    result = resolve_transport(
        gate, q, endpoint="http://gateway/v1", credential_present=credential
    )
    if gate == "direct":
        assert result.transport == (
            Transport.DIRECT
            if q in {Q.PROTOCOL_PROBED, Q.DISCOVERED, Q.CONSUMER_VERIFIED}
            else Transport.UNAVAILABLE
        )
    elif q == Q.PROTOCOL_PROBED and credential:
        assert result.transport == Transport.GATEWAY
    else:
        assert result.transport == Transport.UNAVAILABLE


def test_direct_exception_is_reported():
    DIRECT_EXCEPTIONS[Operation.STT] = "approved test exception"
    try:
        assert (
            resolve_transport(
                "gateway", Q.DISCOVERED, operation=Operation.STT
            ).transport
            == Transport.DIRECT
        )
    finally:
        DIRECT_EXCEPTIONS.clear()


def test_precedence_ladder_and_stale_previous():
    candidates = [("a", "one", Q.DISCOVERED), ("b", "two", Q.CONSUMER_VERIFIED)]
    assert select(Operation.STT, candidates, override=("a", "one")).provider == "a"
    assert (
        select(
            Operation.STT, candidates, previously_selected=("a", "one", Q.DISCOVERED)
        ).provider
        == "a"
    )
    assert (
        select(
            Operation.STT, candidates, previously_selected=("a", "one", Q.UNKNOWN)
        ).provider
        == "b"
    )
    assert (
        select(
            Operation.STT, candidates, preference=[("a", "one"), ("b", "two")]
        ).provider
        == "b"
    )


def test_fresh_process_tier_change_and_cross_consumer_agreement():
    candidates = [("new-tier", "model", Q.PROTOCOL_PROBED)]
    selected = select(
        Operation.EMBEDDING,
        candidates,
        previously_selected=("old-tier", "missing", Q.CONSUMER_VERIFIED),
    )
    assert selected.provider == "new-tier"
    assert select(Operation.EMBEDDING, candidates) == select(
        Operation.EMBEDDING, candidates
    )


def test_five_states_alias_membership_and_no_stale_success():
    assert Q.UNKNOWN != Q.DISCOVERED
    assert (
        select(Operation.TTS, [("p", "listed", Q.UNKNOWN)]).qualification
        == Q.UNAVAILABLE
    )
    # Qualification is an input re-derived per call; no result is cached.
    assert (
        select(Operation.TTS, [("p", "listed", Q.CONSUMER_VERIFIED)]).qualification
        == Q.CONSUMER_VERIFIED
    )
    assert (
        select(Operation.TTS, [("p", "listed", Q.UNKNOWN)]).qualification
        == Q.UNAVAILABLE
    )


def test_render_invariant():
    selection = CanonicalSelection(
        Operation.STT, "p", "m", Q.PROTOCOL_PROBED, "fixture"
    )
    route = RouteResolution(Transport.GATEWAY, "https://proxy/v1", "alias", "STT_KEY")
    locator = ServiceLocator(selection, route)
    rendered = {
        "provider": "p",
        "model": "m",
        "transport": Transport.GATEWAY,
        "endpoint": "https://proxy/v1",
        "wire_model": "alias",
    }
    assert_renders_from(locator, rendered)
    render_invariant_test(locator, rendered)
    rendered["endpoint"] = "http://direct/v1"
    with pytest.raises(AssertionError):
        assert_renders_from(locator, rendered)
