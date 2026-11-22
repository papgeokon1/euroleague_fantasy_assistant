"""Runtime settings, loaded from environment variables or a local .env file."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ELF_", extra="ignore")

    data_dir: Path = PROJECT_ROOT / "data"
    season_code: str = "E2026"  # EuroLeague API season code for 2026-27
    anthropic_api_key: str | None = None
    # Bearer token from a logged-in fantasy session ("<id>|<secret>"). See docs/FANTASY_API.md.
    fantasy_token: SecretStr | None = None
    fantasy_league_id: int = 10  # 10 = EuroLeague, 11 = EuroCup

    models_dir: Path = PROJECT_ROOT / "models"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def db_path(self) -> Path:
        return self.processed_dir / "elfantasy.duckdb"


settings = Settings()
