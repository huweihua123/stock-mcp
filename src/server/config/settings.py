"""
Author: weihua hu
Date: 2025-11-21 22:27:40
LastEditTime: 2025-11-22 18:03:41
LastEditors: weihua hu
Description:
"""

# src/server/config/settings.py
"""Application configuration using pydantic-settings.
Includes MCP host/port, Redis, Tushare configs, and logging settings.
"""

from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Base configuration class with common settings
class BaseAppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


class RedisConfig(BaseAppSettings):
    host: str = Field("localhost", validation_alias="REDIS_HOST")
    port: int = Field(6379, validation_alias="REDIS_PORT")
    db: int = Field(0, validation_alias="REDIS_DB")
    password: Optional[str] = Field(None, validation_alias="REDIS_PASSWORD")
    pool_size: int = Field(10, validation_alias="REDIS_POOL_SIZE")


class MCPConfig(BaseAppSettings):
    host: str = Field("0.0.0.0", validation_alias="MCP_HOST")
    port: int = Field(8000, validation_alias="MCP_PORT")


class TushareConfig(BaseAppSettings):
    token: str = Field("", validation_alias="TUSHARE_TOKEN")
    enabled: bool = Field(False, validation_alias="TUSHARE_ENABLED")
    
    @property
    def is_available(self) -> bool:
        """Check if Tushare is both enabled and has a valid token."""
        return self.enabled and bool(self.token)


class FinnhubConfig(BaseAppSettings):
    api_key: str = Field("", validation_alias="FINNHUB_API_KEY")
    enabled: bool = Field(False, validation_alias="FINNHUB_ENABLED")

    @property
    def is_available(self) -> bool:
        """Check if Finnhub is both enabled and has a valid API key."""
        return self.enabled and bool(self.api_key)


class BaostockConfig(BaseAppSettings):
    """Baostock configuration (free, no API key needed)"""
    
    enabled: bool = Field(True, validation_alias="BAOSTOCK_ENABLED")


class APIKeysConfig(BaseAppSettings):
    """External API keys for various data sources"""

    finnhub: Optional[str] = Field(None, validation_alias="FINNHUB_API_KEY")
    alpha_vantage: Optional[str] = Field(None, validation_alias="ALPHA_VANTAGE_API_KEY")
    twelve_data: Optional[str] = Field(None, validation_alias="TWELVE_DATA_API_KEY")
    news_api: Optional[str] = Field(None, validation_alias="NEWS_API_KEY")
    tavily: Optional[str] = Field(None, validation_alias="TAVILY_API_KEY")
    google: Optional[str] = Field(None, validation_alias="GOOGLE_API_KEY")
    openrouter: Optional[str] = Field(None, validation_alias="OPENROUTER_API_KEY")

    # Search provider configuration
    web_search_provider: str = Field("google", validation_alias="WEB_SEARCH_PROVIDER")


class ProxyConfig(BaseAppSettings):
    """HTTP/HTTPS proxy configuration for external API calls"""
    
    host: str = Field("127.0.0.1", validation_alias="PROXY_HOST")
    port: int = Field(7890, validation_alias="PROXY_PORT")
    enabled: bool = Field(False, validation_alias="PROXY_ENABLED")


class PostgresConfig(BaseAppSettings):
    """PostgreSQL configuration for Security Master and other persistence."""

    dsn: str = Field("", validation_alias="DATABASE_URL")
    host: str = Field("localhost", validation_alias="POSTGRES_HOST")
    port: int = Field(5432, validation_alias="POSTGRES_PORT")
    user: str = Field("postgres", validation_alias="POSTGRES_USER")
    password: str = Field("", validation_alias="POSTGRES_PASSWORD")
    database: str = Field("valuecell", validation_alias="POSTGRES_DB")
    pool_min: int = Field(1, validation_alias="POSTGRES_POOL_MIN")
    pool_max: int = Field(10, validation_alias="POSTGRES_POOL_MAX")

    @property
    def is_configured(self) -> bool:
        return bool(self.dsn or self.database)

    def build_dsn(self) -> str:
        if self.dsn:
            return self.dsn
        return (
            f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"
        )

    def build_asyncpg_dsn(self) -> str:
        dsn = self.build_dsn()
        if dsn.startswith("postgresql+asyncpg://"):
            dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        return dsn


class Settings(BaseAppSettings):
    redis: RedisConfig = Field(default_factory=RedisConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    tushare: TushareConfig = Field(default_factory=TushareConfig)
    finnhub: FinnhubConfig = Field(default_factory=FinnhubConfig)
    baostock: BaostockConfig = Field(default_factory=BaostockConfig)
    api_keys: APIKeysConfig = Field(default_factory=APIKeysConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    security_master_backend: str = Field(
        default="auto", validation_alias="SECURITY_MASTER_BACKEND"
    )
    security_master_sqlite_path: str = Field(
        default="data/security_master.sqlite",
        validation_alias="SECURITY_MASTER_SQLITE_PATH",
    )

    # Other optional configs
    cors_origins: str = Field(default="*", validation_alias="CORS_ORIGINS")

    # Override model_config to add nested delimiter
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )


def get_settings() -> Settings:
    """Helper to get a singleton settings instance"""
    return Settings()
