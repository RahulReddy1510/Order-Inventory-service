from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/inventory"
    redis_url: str = "redis://localhost:6379/0"

    # --- Transactional (OLTP) pool: storefront reads + warehouse/order writes ---
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout: float = 5.0          # seconds to wait for a free connection
    db_statement_timeout_ms: int = 5_000  # kills runaway OLTP queries

    # --- Reporting pool: deliberately small and isolated ---
    report_pool_size: int = 3
    report_max_overflow: int = 0
    report_pool_timeout: float = 10.0
    report_statement_timeout_ms: int = 30_000

    # Short lock wait for order placement: fail fast instead of piling up.
    order_lock_timeout_ms: int = 3_000

    search_index_name: str = "idx:products"


@lru_cache
def get_settings() -> Settings:
    return Settings()
