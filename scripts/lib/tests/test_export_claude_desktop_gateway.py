import importlib.util
import io
import plistlib
import subprocess
import sys
from contextlib import redirect_stderr
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "export-claude-desktop-gateway.py"
SPEC = importlib.util.spec_from_file_location("claude_desktop_gateway_export", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)
FIELDS = {
    "inferenceProvider",
    "inferenceGatewayBaseUrl",
    "inferenceModels",
    "inferenceCredentialKind",
    "inferenceCredentialHelper",
    "inferenceGatewayAuthScheme",
}
PROFILE_METADATA = {
    "PayloadType",
    "PayloadVersion",
    "PayloadIdentifier",
    "PayloadUUID",
    "PayloadDisplayName",
    "PayloadEnabled",
    "PayloadDescription",
}


def run_export(tmp_path, *args):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return (
        subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            check=False,
        ),
        home,
    )


def test_exports_stable_secret_free_profile_without_home_side_effects(tmp_path):
    args = [
        "--endpoint",
        "http://127.0.0.1:4000",
        "--model",
        "vendor/exact-id",
        "--model",
        "vendor/second",
        "--helper",
        "/opt/credential-helper",
    ]
    first, home = run_export(tmp_path, *args)
    second, _ = run_export(tmp_path, *args)

    assert first.returncode == 0
    assert first.stdout == second.stdout
    envelope = plistlib.loads(first.stdout)
    assert set(envelope) == {
        "PayloadType",
        "PayloadVersion",
        "PayloadIdentifier",
        "PayloadUUID",
        "PayloadDisplayName",
        "PayloadContent",
    }
    assert envelope["PayloadType"] == "Configuration"
    assert envelope["PayloadVersion"] == 1
    assert envelope["PayloadIdentifier"] == "com.dotfiles.claude-desktop.gateway"
    assert len(envelope["PayloadContent"]) == 1
    profile = envelope["PayloadContent"][0]
    assert profile["PayloadType"] == "com.anthropic.claudefordesktop"
    assert profile["PayloadUUID"] != envelope["PayloadUUID"]
    assert set(profile) == PROFILE_METADATA | FIELDS
    assert "Payload" not in profile
    assert "PayloadContent" not in profile
    assert "inferenceGateway" not in profile
    assert profile["inferenceProvider"] == "gateway"
    assert profile["inferenceGatewayBaseUrl"] == "http://127.0.0.1:4000"
    assert profile["inferenceModels"] == (
        '[{"name":"vendor/exact-id"},{"name":"vendor/second"}]'
    )
    assert profile["inferenceCredentialKind"] == "helper-script"
    assert profile["inferenceCredentialHelper"] == "/opt/credential-helper"
    assert profile["inferenceGatewayAuthScheme"] == "bearer"
    assert "inferenceGatewayApiKey" not in profile
    assert not (home / "Library").exists()


def test_main_emits_parseable_profile_in_process(monkeypatch):
    output = io.BytesIO()

    class Stdout:
        buffer = output

    monkeypatch.setattr(EXPORTER.sys, "stdout", Stdout())
    result = EXPORTER.main(
        [
            "--endpoint",
            "http://127.0.0.1:4000/",
            "--model",
            "vendor/model",
            "--helper",
            "/opt/helper",
        ]
    )

    assert result == 0
    profile = plistlib.loads(output.getvalue())["PayloadContent"][0]
    assert profile["inferenceGatewayBaseUrl"] == "http://127.0.0.1:4000"
    assert profile["inferenceModels"] == '[{"name":"vendor/model"}]'


def test_main_reports_invalid_model_in_process(monkeypatch):
    errors = io.StringIO()
    monkeypatch.setattr(EXPORTER.sys, "stderr", errors)

    with redirect_stderr(errors), pytest.raises(SystemExit) as error:
        EXPORTER.main(
            [
                "--endpoint",
                "http://127.0.0.1:4000",
                "--model",
                "model id",
                "--helper",
                "/opt/helper",
            ]
        )

    assert error.value.code == 2
    assert "model ids must not contain whitespace" in errors.getvalue()


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--endpoint", "https://127.0.0.1:4000"),
        ("--endpoint", "http://localhost:4000"),
        ("--endpoint", "http://user:secret@127.0.0.1:4000"),
        ("--endpoint", "http://127.0.0.1:4000/v1"),
        ("--endpoint", "http://127.0.0.1:0"),
        ("--endpoint", "http://127.0.0.1:notaport"),
        ("--helper", "relative-helper"),
        ("--helper", "/helper\n--injected"),
        ("--model", "model\nforged"),
        ("--model", "model\u007f"),
        ("--model", "model id"),
    ],
)
def test_rejects_unsafe_values(tmp_path, option, value):
    result, _ = run_export(
        tmp_path,
        "--endpoint",
        "http://127.0.0.1:4000",
        "--model",
        "exact/model",
        "--helper",
        "/helper",
        option,
        value,
    )
    assert result.returncode == 2


def test_no_static_key_option(tmp_path):
    result, _ = run_export(
        tmp_path,
        "--endpoint",
        "http://127.0.0.1:4000",
        "--model",
        "exact/model",
        "--helper",
        "/helper",
        "--api-key",
        "must-not-be-accepted",
    )
    assert result.returncode == 2


def test_exports_stable_secret_free_profile_in_process(monkeypatch):
    import runpy

    output = io.BytesIO()

    class Stdout:
        buffer = output

    monkeypatch.setattr(sys, "stdout", Stdout())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--endpoint",
            "http://127.0.0.1:4000",
            "--model",
            "vendor/exact-id",
            "--model",
            "vendor/second",
            "--helper",
            "/opt/credential-helper",
        ],
    )

    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(SCRIPT), run_name="__main__")

    assert result.value.code == 0
    profile = plistlib.loads(output.getvalue())["PayloadContent"][0]
    assert profile["inferenceModels"] == (
        '[{"name":"vendor/exact-id"},{"name":"vendor/second"}]'
    )
    assert "inferenceGatewayApiKey" not in profile
