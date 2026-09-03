from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://lensplan:lensplan@localhost:5435/lensplan"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 720

    max_solve_seconds: float = 120.0
    default_solve_seconds: float = 90.0
    default_mip_gap: float = 1e-4
    max_anneal_iterations: int = 2000


@lru_cache
def get_settings() -> Settings:
    return Settings()
