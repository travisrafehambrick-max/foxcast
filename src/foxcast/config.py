"""Settings: non-secret values from a TOML file, keys only from environment variables.

Environment variables use the ``FOXCAST_`` prefix and override the file. The file may never
hold a key, so a config file can be shared or committed without leaking secrets.
"""

import json
import tomllib
from pathlib import Path
from typing import Any, Final

from pydantic import Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

ENV_PREFIX: Final = "FOXCAST_"
SECRET_FIELDS: Final = frozenset({"fred_api_key"})
DEFAULT_WATCHLIST_SIZE: Final = 10
MAX_WATCHLIST_SIZE: Final = 500


def default_home() -> Path:
    return Path.home() / ".foxcast"


def default_config_path() -> Path:
    return default_home() / "foxcast.toml"


class Settings(BaseSettings):
    """Everything foxcast needs to know about the machine it runs on."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="forbid", frozen=True)

    sec_contact: str | None = Field(
        default=None,
        description="SEC fair-access contact: a name and email address, sent as User-Agent.",
    )
    fred_api_key: SecretStr | None = None
    data_dir: Path = Field(default_factory=lambda: default_home() / "data")
    watchlist_size: int = Field(default=DEFAULT_WATCHLIST_SIZE, ge=1, le=MAX_WATCHLIST_SIZE)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Environment first, so it overrides values passed in from the config file.
        return (env_settings, init_settings)


def read_config_file(path: Path) -> dict[str, Any]:
    """Read the TOML config, refusing any key that must come from the environment."""
    if not path.exists():
        return {}
    values = tomllib.loads(path.read_text(encoding="utf-8"))
    # Field names match case-insensitively, with or without the env prefix, so normalise
    # before checking. Never include the value in the message.
    leaked = sorted(
        key for key in values if key.lower().removeprefix(ENV_PREFIX.lower()) in SECRET_FIELDS
    )
    if leaked:
        raise ValueError(
            f"{path} sets {', '.join(leaked)}; keys must come from the environment "
            "(for example FOXCAST_FRED_API_KEY), never from the config file"
        )
    return values


def load_settings(path: Path) -> Settings:
    return Settings(**read_config_file(path))


def write_default_config(path: Path, *, data_dir: Path) -> None:
    """Write a starter config file. Never overwrites an existing one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # A JSON string literal is a valid TOML basic string, so quotes and control
    # characters in the path are escaped rather than breaking the file.
    content = (
        "# foxcast configuration. Keys never go in this file: set FOXCAST_SEC_CONTACT\n"
        "# and FOXCAST_FRED_API_KEY as environment variables instead.\n"
        f"data_dir = {json.dumps(data_dir.as_posix())}\n"
        f"watchlist_size = {DEFAULT_WATCHLIST_SIZE}\n"
    )
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
    except FileExistsError as exc:
        raise FileExistsError(f"{path} already exists; not overwriting it") from exc
