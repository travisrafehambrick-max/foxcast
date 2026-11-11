"""Config: non-secret settings from foxcast.toml, keys only from the environment."""

from pathlib import Path

import pytest

from foxcast.config import Settings, load_settings, write_default_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FOXCAST_SEC_CONTACT", "FOXCAST_FRED_API_KEY", "FOXCAST_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.unit
def test_defaults_without_a_config_file(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "missing.toml")
    assert settings.sec_contact is None
    assert settings.fred_api_key is None
    assert settings.watchlist_size == 10


@pytest.mark.unit
def test_keys_come_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOXCAST_SEC_CONTACT", "Jane Doe jane@example.com")
    monkeypatch.setenv("FOXCAST_FRED_API_KEY", "abc123")
    settings = load_settings(tmp_path / "missing.toml")
    assert settings.sec_contact == "Jane Doe jane@example.com"
    assert settings.fred_api_key is not None
    assert settings.fred_api_key.get_secret_value() == "abc123"
    assert "abc123" not in repr(settings)


@pytest.mark.unit
def test_config_file_sets_non_secret_values(tmp_path: Path) -> None:
    config = tmp_path / "foxcast.toml"
    config.write_text(f'data_dir = "{(tmp_path / "d").as_posix()}"\nwatchlist_size = 50\n')
    settings = load_settings(config)
    assert settings.data_dir == tmp_path / "d"
    assert settings.watchlist_size == 50


@pytest.mark.unit
def test_config_file_may_not_hold_keys(tmp_path: Path) -> None:
    config = tmp_path / "foxcast.toml"
    config.write_text('fred_api_key = "leaked"\n')
    with pytest.raises(ValueError, match="environment"):
        load_settings(config)


@pytest.mark.unit
def test_write_default_config_creates_file_without_secrets(tmp_path: Path) -> None:
    config = tmp_path / "foxcast.toml"
    write_default_config(config, data_dir=tmp_path / "data")
    text = config.read_text(encoding="utf-8")
    assert "data_dir" in text
    assert "key" not in text.split("#")[0]
    assert isinstance(load_settings(config), Settings)


@pytest.mark.unit
def test_write_default_config_refuses_to_overwrite(tmp_path: Path) -> None:
    config = tmp_path / "foxcast.toml"
    config.write_text("watchlist_size = 3\n")
    with pytest.raises(FileExistsError):
        write_default_config(config, data_dir=tmp_path / "data")
