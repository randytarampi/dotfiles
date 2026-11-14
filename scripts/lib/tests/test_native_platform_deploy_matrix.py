"""The required native deployment matrix must not silently drop OS/CPU rows."""

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/ci.yml"


def _deploy_job():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    return workflow.split("  deploy:", 1)[1].split("  bootstrap-smoke:", 1)[0]


def test_required_native_deploy_matrix_has_explicit_windows_and_macos_cpu_rows():
    deploy = _deploy_job()
    expected = (
        ("ubuntu-latest", "x86_64"),
        ("macos-latest", "arm64"),
        ("macos-15-intel", "x86_64"),
        ("macos-26", "arm64"),
        ("windows-latest", "X64"),
        ("windows-11-arm", "arm64"),
    )
    for name, arch in expected:
        marker = f"          - os: {name}\n"
        assert marker in deploy
        row = deploy.split(marker, 1)[1].split("\n          - os:", 1)[0]
        assert f"architecture: {arch}" in row

    assert "name: Deploy (${{ matrix.os }})" in deploy
    assert "fail-fast: false" in deploy
    assert "runs-on: ${{ matrix.os }}" in deploy
    assert "continue-on-error" not in deploy


def test_native_cpu_checks_run_before_any_package_or_user_state_mutation():
    deploy = _deploy_job()
    source = deploy.split("    steps:", 1)[1]
    assert source.index("Assert native Unix CPU") < source.index("Install Poetry")
    assert source.index("Assert native Windows kernel") < source.index("Install Poetry")
    assert 'test "$(uname -m)" = "$EXPECTED_ARCH"' in deploy
    assert "sw_vers -productVersion" in deploy
    assert "OSArchitecture.ToString()" in deploy
    assert "Get-CimInstance Win32_OperatingSystem" in deploy
    assert "persist-credentials: false" in deploy


def test_known_macos_and_windows_release_mappings_are_not_arch_inferred():
    deploy = _deploy_job()
    assertions = (
        ("macos-15-intel", 'macos_major: "15"'),
        ("macos-26", 'macos_major: "26"'),
        ("windows-latest", "windows_caption: Windows Server"),
        ("windows-11-arm", "windows_caption: Windows 11"),
    )
    for name, required in assertions:
        row = deploy.split(f"          - os: {name}\n", 1)[1].split(
            "\n          - os:", 1
        )[0]
        assert required in row


def test_native_clients_keep_original_two_pass_deploy_and_required_aggregate():
    deploy = _deploy_job()
    assert "Configure chezmoi script interpreter (Windows)" in deploy
    assert "Install locked production Python dependencies" in deploy
    assert "Prepare fresh home and empty environment" in deploy
    assert "Deploy (first pass)" in deploy
    assert "Deploy (idempotency pass)" in deploy
    assert "Chezmoi no-op check" in deploy
    workflow = WORKFLOW.read_text(encoding="utf-8")
    required = workflow.split("  ci-required:", 1)[1]
    assert "name: ci/required" in required
    assert "needs: [verify, deploy, bootstrap-smoke, bootstrap-networkless]" in required
    assert 'if [[ "$result" != "success" ]]' in required
