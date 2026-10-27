import litellm_config

FIELDS = {
    "client",
    "env_name",
    "key_file",
    "install_signatures",
    "transports",
    "config_owner",
    "setup_gates",
    "auth_source",
    "consumer_read_path",
    "classification",
    "credential_policy",
    "eligibility",
    "classification_status",
    "evidence",
    "free_route_policy",
}


def test_registry_rows_are_complete():
    assert litellm_config.CLIENT_REGISTRY
    for row in litellm_config.CLIENT_REGISTRY:
        assert set(row) == FIELDS
        for field, value in row.items():
            assert value not in (None, "", [], {}), (row["client"], field)
        assert set(row["eligibility"]) == {
            "setup_gate",
            "installed",
            "user_enabled",
            "verified_usable_transport",
        }
        assert row["free_route_policy"] == {
            "model_name_contains_free": True,
            "provider_server_of_record_only": ["cohere", "poolside"],
            "third_party_hosting_excluded": True,
            "classifier": "scripts/lib/free_classifier.py:evaluate",
        }


def test_app_keys_have_matching_registry_env_and_key_path():
    rows = {row["client"]: row for row in litellm_config.CLIENT_REGISTRY}
    for client, env_name in litellm_config.APP_KEYS.items():
        assert rows[client]["env_name"] == env_name
        assert (
            rows[client]["key_file"] == f"~/.local/share/litellm/clients/{client}.key"
        )


def test_setup_gates_and_classification_only_credentials():
    rows = {row["client"]: row for row in litellm_config.CLIENT_REGISTRY}
    assert rows["pi"]["setup_gates"] == [
        "DOTFILES_USE_LITELLM_PROXY=1",
        "DOTFILES_RUN_LITELLM_SETUP=1",
    ]
    for client in ("opencode", "openwebui", "junie"):
        assert rows[client]["setup_gates"] == ["DOTFILES_RUN_LITELLM_SETUP=1"]
    assert rows["claude"]["setup_gates"] == [
        "DOTFILES_RUN_CLAUDE_SETUP=1",
        "DOTFILES_RUN_LITELLM_SETUP=1",
    ]
    for row in litellm_config.CLIENT_REGISTRY:
        if row["classification"] == "classification-only":
            assert row["credential_policy"] == "no-credential"


def test_cursor_is_non_routable_without_credential():
    row = next(
        row for row in litellm_config.CLIENT_REGISTRY if row["client"] == "cursor"
    )
    assert row["credential_policy"] == "no-credential"
    assert row["classification"] == "classification-only"
    assert row["classification_status"] == "classification-only; not-routable"


def test_mozart_is_existing_consumer_with_evidence():
    row = next(
        row for row in litellm_config.CLIENT_REGISTRY if row["client"] == "mozart"
    )
    assert row["classification"] == "eligible"
    assert row["classification_status"] == "existing-consumer"
    assert row["credential_policy"] == "no-credential"
    assert all(":" in pointer for pointer in row["evidence"])


def test_hermes_is_config_only_with_pool_delivery_deferred():
    row = next(
        row for row in litellm_config.CLIENT_REGISTRY if row["client"] == "hermes"
    )
    assert row["classification"] == "config-only"
    assert row["credential_policy"] == "defer"
    assert row["config_owner"] == "scripts/configure-hermes.py"
    assert row["setup_gates"] == [
        "DOTFILES_RUN_HERMES_SETUP=1",
        "DOTFILES_USE_LITELLM_PROXY=1",
    ]
    assert "pool" in row["classification_status"]


def test_each_row_encodes_four_condition_eligibility():
    for row in litellm_config.CLIENT_REGISTRY:
        assert set(row["eligibility"]) == {
            "setup_gate",
            "installed",
            "user_enabled",
            "verified_usable_transport",
        }
        assert all(
            isinstance(value, str) and value for value in row["eligibility"].values()
        )
