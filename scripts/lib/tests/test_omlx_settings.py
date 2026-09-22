import importlib.util
from pathlib import Path

import pytest

import local_engines


def _load_verify_config():
    path = Path(__file__).resolve().parents[3] / "scripts" / "verify-config.py"
    spec = importlib.util.spec_from_file_location("verify_config", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unitless_audio_upload_size_is_refused_as_bytes_ambiguous():
    with pytest.raises(ValueError, match="Unitless"):
        local_engines.merge_omlx_settings(
            {"server": {"max_audio_upload_size": "128"}}, {}
        )


def test_valid_audio_upload_size_passes_through():
    settings = local_engines.merge_omlx_settings(
        {"server": {"max_audio_upload_size": "128MB"}}, {}
    )
    assert settings["server"]["max_audio_upload_size"] == "128MB"


def test_missing_audio_upload_size_is_untouched():
    settings = local_engines.merge_omlx_settings({}, {})
    assert "max_audio_upload_size" not in settings["server"]


def test_malformed_audio_upload_size_raises_actionable_error():
    with pytest.raises(ValueError, match="max_audio_upload_size"):
        local_engines.merge_omlx_settings(
            {"server": {"max_audio_upload_size": "not-a-size"}}, {}
        )


def test_audio_upload_size_environment_override_is_respected():
    settings = local_engines.merge_omlx_settings(
        {"server": {"max_audio_upload_size": "64MB"}},
        {"OMLX_MAX_AUDIO_UPLOAD_SIZE": "256MB"},
    )
    assert settings["server"]["max_audio_upload_size"] == "256MB"


def test_read_only_check_warns_for_unitless_and_accepts_missing_or_suffixed():
    verify = _load_verify_config()
    missing = local_engines.merge_omlx_settings({}, {})
    assert "max_audio_upload_size" not in missing["server"]
    assert verify.validate_omlx_settings(missing) == []
    suffixed = local_engines.merge_omlx_settings({}, {})
    suffixed["server"]["max_audio_upload_size"] = "128MB"
    assert verify.validate_omlx_settings(suffixed) == []
    unitless = dict(suffixed)
    unitless["server"] = dict(suffixed["server"])
    unitless["server"]["max_audio_upload_size"] = "128"
    assert verify.omlx_settings_warnings(unitless)
