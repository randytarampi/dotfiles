import pytest

import local_engines


def test_unitless_audio_upload_size_warns_and_preserves_existing(caplog):
    existing = {"server": {"max_audio_upload_size": "128"}}
    settings = local_engines.merge_omlx_settings(existing, {})
    assert settings["server"]["max_audio_upload_size"] == "128"
    assert "preserving existing setting" in caplog.text


def test_valid_audio_upload_size_passes_through():
    settings = local_engines.merge_omlx_settings(
        {"server": {"max_audio_upload_size": "128MB"}}, {}
    )
    assert settings["server"]["max_audio_upload_size"] == "128MB"


def test_missing_audio_upload_size_is_untouched():
    settings = local_engines.merge_omlx_settings({}, {})
    assert "max_audio_upload_size" not in settings["server"]


def test_malformed_audio_upload_size_warns_and_preserves_existing(caplog):
    existing = {"server": {"max_audio_upload_size": "not-a-size"}}
    settings = local_engines.merge_omlx_settings(existing, {})
    assert settings["server"]["max_audio_upload_size"] == "not-a-size"
    assert "preserving existing setting" in caplog.text


def test_audio_upload_size_environment_override_is_respected():
    settings = local_engines.merge_omlx_settings(
        {"server": {"max_audio_upload_size": "64MB"}},
        {"OMLX_MAX_AUDIO_UPLOAD_SIZE": "256MB"},
    )
    assert settings["server"]["max_audio_upload_size"] == "256MB"


def test_invalid_audio_upload_environment_warns_and_preserves_file_value(caplog):
    settings = local_engines.merge_omlx_settings(
        {"server": {"max_audio_upload_size": "64MB"}},
        {"OMLX_MAX_AUDIO_UPLOAD_SIZE": "128"},
    )
    assert settings["server"]["max_audio_upload_size"] == "64MB"
    assert "preserving existing setting" in caplog.text


def test_read_only_check_flags_unitless_and_accepts_missing_or_suffixed():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "scripts" / "verify-config.py"
    spec = importlib.util.spec_from_file_location("verify_config", path)
    assert spec is not None and spec.loader is not None
    verify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify)
    missing = local_engines.merge_omlx_settings({}, {})
    assert "max_audio_upload_size" not in missing["server"]
    assert verify.validate_omlx_settings(missing) == []
    suffixed = local_engines.merge_omlx_settings({}, {})
    suffixed["server"]["max_audio_upload_size"] = "128MB"
    assert verify.validate_omlx_settings(suffixed) == []
    unitless = dict(suffixed)
    unitless["server"] = dict(suffixed["server"])
    unitless["server"]["max_audio_upload_size"] = "128"
    assert any(
        "unitless values are refused" in error
        for error in verify.validate_omlx_settings(unitless)
    )
