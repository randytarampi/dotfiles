from model_references import (
    Catalogue,
    EndpointIdentity,
    ModelReference,
    Outcome,
    compare_references,
    google_direct_id,
    openai_zen_direct_id,
)


def ref(namespace, credential, model, source="fixture", field="model"):
    return ModelReference(EndpointIdentity(namespace, credential), model, source, field)


def outcomes(comparison):
    return {result.reference.wire_id: result.outcome for result in comparison.results}


def test_litellm_google_alias_is_exact_not_same_leaf():
    requested = ref("litellm", "junie-primary", "google/gemini-3.8-flash")
    endpoint = requested.endpoint
    result = compare_references(
        [requested],
        {endpoint: Catalogue(True, frozenset({"other/gemini-3.8-flash"}))},
    )
    assert outcomes(result)[requested.wire_id] == Outcome.MISSING


def test_openrouter_inner_slashes_and_tags_are_preserved():
    model = "openrouter/inclusionai/ling-3.0-flash-sante:free"
    requested = ref("openrouter", "review-key", model)
    result = compare_references(
        [requested],
        {requested.endpoint: Catalogue(True, frozenset({model}))},
    )
    assert outcomes(result)[model] == Outcome.MATCH


def test_direct_openai_ids_are_exact_versioned_and_strip_one_wrapper():
    requested = [
        ref("openai-direct", "openai-key", "openai/gpt-6-sol"),
        ref("openai-direct", "openai-key", "openai/gpt-6.1-sol"),
        ref("zen-direct", "zen-key", "opencode/zen-model"),
    ]
    result = compare_references(
        requested,
        {
            item.endpoint: Catalogue(True, frozenset({"gpt-6-sol", "zen-model"}))
            for item in requested
        },
        normalize_id=lambda endpoint, model: (
            openai_zen_direct_id(model)
            if endpoint.namespace in {"openai-direct", "zen-direct"}
            else model
        ),
    )
    assert outcomes(result) == {
        "openai/gpt-6-sol": Outcome.MATCH,
        "openai/gpt-6.1-sol": Outcome.MISSING,
        "opencode/zen-model": Outcome.MATCH,
    }
    assert openai_zen_direct_id("openai/opencode/model") == "opencode/model"


def test_google_direct_resource_prefix_is_adapter_specific():
    direct = ref("google-direct", "google-key", "models/gemini-3.8-flash")
    proxy = ref("litellm", "junie-key", "google/gemini-3.8-flash")
    result = compare_references(
        [direct, proxy],
        {
            direct.endpoint: Catalogue(True, frozenset({"gemini-3.8-flash"})),
            proxy.endpoint: Catalogue(True, frozenset({"gemini-3.8-flash"})),
        },
        normalize_id=lambda endpoint, model: (
            google_direct_id(model) if endpoint.namespace == "google-direct" else model
        ),
    )
    assert outcomes(result)[direct.wire_id] == Outcome.MATCH
    assert outcomes(result)[proxy.wire_id] == Outcome.MISSING


def test_meridian_bare_alias_is_only_compared_with_meridian():
    meridian = ref("meridian", "meridian-key", "claude-sonnet-4.6")
    anthropic = ref("anthropic-direct", "anthropic-key", "claude-sonnet-4.6")
    result = compare_references(
        [meridian, anthropic],
        {
            meridian.endpoint: Catalogue(True, frozenset({"claude-sonnet-4.6"})),
            anthropic.endpoint: Catalogue(True, frozenset()),
        },
    )
    by_endpoint = {item.reference.endpoint: item.outcome for item in result.results}
    assert by_endpoint[meridian.endpoint] == Outcome.MATCH
    assert by_endpoint[anthropic.endpoint] == Outcome.MISSING


def test_unknown_auth_and_known_missing_are_independent():
    missing = ref("litellm", "junie-primary", "google/gemini-3.8-flash")
    unknown = ref("openrouter", "review-key", "vendor/model")
    result = compare_references(
        [missing, unknown],
        {
            missing.endpoint: Catalogue(True, frozenset({"other/gemini-3.8-flash"})),
            unknown.endpoint: Catalogue(False, http_status=401, reason="HTTP 401"),
        },
    )
    assert [item.outcome for item in result.results] == [
        Outcome.MISSING,
        Outcome.UNKNOWN,
    ]
    assert result.complete is False
    assert result.results[1].reason == "HTTP 401"


def test_empty_successful_catalogue_is_missing_and_inactive_is_skipped():
    active = ref("ollama-cloud", "local", "ollama/model:cloud")
    inactive = ref("ollama-cloud", "disabled", "ollama-cloud/model")
    result = compare_references(
        [active, inactive],
        {active.endpoint: Catalogue(True, frozenset())},
        inactive=frozenset({inactive.endpoint}),
    )
    by_endpoint = {item.reference.endpoint: item.outcome for item in result.results}
    assert by_endpoint[active.endpoint] == Outcome.MISSING
    assert by_endpoint[inactive.endpoint] == Outcome.SKIPPED_INACTIVE
    assert result.complete is True


def test_same_model_text_does_not_cross_endpoint_or_credential_scope():
    local = ref("ollama-local", "workstation", "ollama/model:cloud")
    cloud = ref("ollama-cloud", "cloud-account", "ollama/model:cloud")
    second_key = ref("ollama-cloud", "second-account", "ollama/model:cloud")
    result = compare_references(
        [local, cloud, second_key],
        {
            local.endpoint: Catalogue(True, frozenset({"ollama/model:cloud"})),
            cloud.endpoint: Catalogue(True, frozenset()),
        },
    )
    by_endpoint = {item.reference.endpoint: item.outcome for item in result.results}
    assert by_endpoint[local.endpoint] == Outcome.MATCH
    assert by_endpoint[cloud.endpoint] == Outcome.MISSING
    assert by_endpoint[second_key.endpoint] == Outcome.UNKNOWN
