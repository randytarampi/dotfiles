import importlib.util
from pathlib import Path

import pytest

import caddy_domains

ROOT = Path(__file__).resolve().parents[3]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_litellm_site_blocks_are_ui_only_and_auth_matcher_is_local():
    caddy = load_script("configure-caddy")
    domains = [
        "local.randytarampi.ca",
        "local.randytarampi.com",
        "shush.randytarampi.ca",
        "shush.randytarampi.com",
    ]
    auth = caddy.build_auth_block([("caddy", "hashed-password")])
    blocks = caddy.build_litellm_site_blocks(
        domains,
        "lan",
        True,
        "0.0.0.0",
        "/tmp/fullchain.pem",
        "/tmp/key.pem",
        auth,
        "4000",
        "443",
    )

    assert len(blocks) == 4
    for block in blocks:
        lines = block.splitlines()
        auth_index = next(
            index
            for index, line in enumerate(lines)
            if "basic_auth @not_omlx {" in line
        )
        matcher_index = next(
            index
            for index, line in enumerate(lines)
            if "@not_omlx not path /omlx/*" in line
        )
        assert matcher_index < auth_index
        assert "@not_lan not remote_ip private_ranges" in block
        assert "abort @not_lan" in block
        assert "@litellm_restricted path /v1 /v1/* /key/* /health/*" in block
        assert "respond @litellm_restricted 403" in block
        assert "@litellm_ui path /ui /ui/* /logo/*" in block
        assert "reverse_proxy 127.0.0.1:4000" in block
        assert "handle / {" not in block
        assert "/sso/key/generate" not in block


@pytest.mark.parametrize(
    "access_mode,exposed,expected",
    [
        ("localhost", False, 0),
        ("localhost", True, 0),
        ("lan", False, 0),
        ("lan", True, 4),
        ("public", False, 0),
        ("public", True, 4),
    ],
)
def test_litellm_site_gate_matrix(access_mode, exposed, expected):
    caddy = load_script("configure-caddy")
    blocks = caddy.build_litellm_site_blocks(
        [
            "local.randytarampi.ca",
            "local.randytarampi.com",
            "shush.randytarampi.ca",
            "shush.randytarampi.com",
        ],
        access_mode,
        exposed,
        "0.0.0.0",
        "/tmp/fullchain.pem",
        "/tmp/key.pem",
        caddy.build_auth_block([("caddy", "hashed-password")]),
        "4000",
        "443",
    )
    assert len(blocks) == expected


def test_wildcard_dns_names_are_not_treated_as_caddy_site_domains(tmp_path):
    zones = tmp_path / "ddns-zones.json"
    zones.write_text(
        '{"zones":[{"records":[{"name":"local.example.ca."},'
        '{"name":"*.local.example.ca."}]}]}',
        encoding="utf-8",
    )
    assert caddy_domains.load_domains(zones) == ["local.example.ca"]


def test_hosts_entries_include_litellm_only_for_exposed_external_sites():
    domains = [
        "local.example.ca",
        "shush.example.ca",
        "local.example.com",
        "shush.example.com",
    ]
    for mode in ("localhost", "lan", "public"):
        enabled = caddy_domains.build_hosts_entries(
            domains, mode, litellm_ui_exposed=True
        )
        names = {line.split()[1] for line in enabled}
        expected = (
            {f"litellm.{domain}" for domain in domains}
            if mode in {"lan", "public"}
            else set()
        )
        assert {name for name in names if name.startswith("litellm.")} == expected


def test_ddns_config_emits_missing_wildcard_records_idempotently():
    ddns = load_script("configure-ddns")
    records = [
        {"name": "local.example.ca.", "type": "A", "ttl": 300},
        {"name": "local.example.ca.", "type": "AAAA", "ttl": 300},
        {"name": "shush.example.ca.", "type": "A", "ttl": 300},
        {"name": "shush.example.ca.", "type": "AAAA", "ttl": 300},
        {"name": "*.local.example.ca.", "type": "A", "ttl": 300},
    ]
    emitted = ddns.add_wildcard_records(records)
    wildcard_records = [record for record in emitted if record["name"].startswith("*.")]
    assert {(record["name"], record["type"]) for record in wildcard_records} == {
        ("*.local.example.ca.", "A"),
        ("*.local.example.ca.", "AAAA"),
        ("*.shush.example.ca.", "A"),
        ("*.shush.example.ca.", "AAAA"),
    }
    assert len(ddns.add_wildcard_records(emitted)) == len(emitted)
