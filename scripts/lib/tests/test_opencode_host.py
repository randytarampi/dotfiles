import subprocess

import opencode_host


def _stub_probe(monkeypatch, result):
    monkeypatch.setattr(opencode_host.shutil, "which", lambda name: f"/usr/bin/{name}")
    runner = result if callable(result) else (lambda *a, **k: result)
    monkeypatch.setattr(opencode_host.subprocess, "run", runner)


def test_resolve_host_known_versions(monkeypatch):
    class Probe:
        def __init__(self, stdout, stderr=""):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = 0

    _stub_probe(monkeypatch, lambda *a, **k: Probe("opencode version 1.19.5\n"))
    assert opencode_host._probe_host("opencode") == ("known", 1, "1.19.5")
    _stub_probe(monkeypatch, lambda *a, **k: Probe("2.2.5\n"))
    assert opencode_host._probe_host("opencode") == ("known", 2, "2.2.5")
    for invalid in ("2.0.21-rc1", "2.0.21+metadata", "2.0.21.1", "v2.0.21junk"):
        _stub_probe(monkeypatch, lambda *a, value=invalid, **k: Probe(value))
        assert opencode_host._probe_host("opencode")[0] == "unknown"
    _stub_probe(monkeypatch, lambda *a, **k: Probe("2.0.21 2.0.31"))
    assert opencode_host._probe_host("opencode")[0] == "unknown"


def test_resolve_host_absent_and_unknown(monkeypatch):
    monkeypatch.setattr(opencode_host.shutil, "which", lambda name: None)
    assert opencode_host._probe_host("opencode") == ("absent", None, None)

    def boom(*args, **kwargs):
        raise subprocess.SubprocessError("crashed")

    _stub_probe(monkeypatch, boom)
    assert opencode_host._probe_host("opencode") == ("unknown", None, None)

    _stub_probe(
        monkeypatch,
        lambda *a, **k: subprocess.CompletedProcess(["opencode"], 1, "2.0.21", ""),
    )
    assert opencode_host._probe_host("opencode") == ("unknown", None, "probe exited 1")


def test_full_version_token_is_preserved(monkeypatch):
    class Probe:
        stdout = "opencode version 2.2.5\n"
        stderr = ""
        returncode = 0

    _stub_probe(monkeypatch, Probe())
    assert opencode_host.host_version_string() == "2.2.5"


def test_native_emission_decision_matrix(capsys):
    notes = []

    class Log:
        @staticmethod
        def warning(message):
            notes.append(message)

    assert opencode_host.native_emission_decision("absent", None, Log)
    assert notes and "No OpenCode binary found" in notes[0]
    notes.clear()
    assert opencode_host.native_emission_decision("known", 2, Log)
    assert not notes
    notes.clear()
    assert not opencode_host.native_emission_decision("known", 1, Log)
    assert notes and "v1" in notes[0]
    notes.clear()
    assert not opencode_host.native_emission_decision("unknown", None, Log)
    assert notes and "could not be probed" in notes[0]


def test_decision_without_logger_never_crashes():
    assert opencode_host.native_emission_decision("absent", None)
    assert opencode_host.native_emission_decision("known", 2)
    assert not opencode_host.native_emission_decision("known", 1)
    assert not opencode_host.native_emission_decision("known", 3)
    assert not opencode_host.native_emission_decision("unknown", None)
    assert not opencode_host.native_emission_decision("known", 3)
