"""Test config: base URL custom & penyimpanan pengaturan permanen (config.json)."""
import pytest

from app import config


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    """Arahkan config.json ke file sementara & bersihkan env terkait."""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return tmp_path


def test_default_base_url(isolated_config):
    assert config.get_base_url() == config.DEFAULT_BASE_URL


def test_save_settings_persists_key_and_base_url(isolated_config):
    config.save_settings(api_key="sk-test", base_url="https://example.com/v1/")
    # Trailing slash dipangkas.
    assert config.get_base_url() == "https://example.com/v1"
    assert config.get_api_key() == "sk-test"


def test_save_settings_empty_base_url_resets_to_default_keeps_key(isolated_config):
    config.save_settings(api_key="k", base_url="https://x.com")
    config.save_settings(api_key=None, base_url="")  # reset base url, biarkan key
    assert config.get_base_url() == config.DEFAULT_BASE_URL
    assert config.get_api_key() == "k"


def test_save_settings_blank_key_keeps_existing(isolated_config):
    config.save_settings(api_key="keep-me", base_url=None)
    config.save_settings(api_key="   ", base_url="https://y.com")  # key kosong -> dipertahankan
    assert config.get_api_key() == "keep-me"
    assert config.get_base_url() == "https://y.com"


def test_save_settings_rejects_non_http_base_url(isolated_config):
    with pytest.raises(ValueError):
        config.save_settings(api_key=None, base_url="ftp://nope")


def test_env_base_url_overrides_file(isolated_config, monkeypatch):
    config.save_settings(api_key=None, base_url="https://from-file.com")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://from-env.com/v2")
    assert config.get_base_url() == "https://from-env.com/v2"
