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


class FinnhubConfig(BaseAppSettings):
    api_key: str = Field("", validation_alias="FINNHUB_API_KEY")


class BaostockConfig(BaseAppSettings):
    """Baostock configuration (free, no API key needed)"""

    enabled: bool = Field(True, validation_alias="BAOSTOCK_ENABLED")


class APIKeysConfig(BaseAppSettings):
    """External API keys for various data sources"""

    finnhub: Optional[str] = Field(None, validation_alias="FINNHUB_API_KEY")
    alpha_vantage: Optional[str] = Field(None, validation_alias="ALPHA_VANTAGE_API_KEY")
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


class NacosConfig(BaseAppSettings):
    """Nacos MCP configuration"""

    server_addr: str = Field("127.0.0.1:8848", validation_alias="NACOS_SERVER_ADDR")
    namespace: str = Field("public", validation_alias="NACOS_NAMESPACE")
    username: str = Field("nacos", validation_alias="NACOS_USERNAME")
    password: str = Field("nacos", validation_alias="NACOS_PASSWORD")

    # MCP Server configuration
    mcp_server_name: str = Field(
        "stock-mcp-server", validation_alias="NACOS_MCP_SERVER_NAME"
    )
    mcp_server_version: str = Field(
        "1.0.0", validation_alias="NACOS_MCP_SERVER_VERSION"
    )
    mcp_server_port: int = Field(18001, validation_alias="NACOS_MCP_SERVER_PORT")
    mcp_transport: str = Field("sse", validation_alias="NACOS_MCP_TRANSPORT")

    # Registration control
    register_enabled: bool = Field(True, validation_alias="NACOS_REGISTER_ENABLED")


class Settings(BaseAppSettings):
    redis: RedisConfig = Field(default_factory=RedisConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    tushare: TushareConfig = Field(default_factory=TushareConfig)
    finnhub: FinnhubConfig = Field(default_factory=FinnhubConfig)
    baostock: BaostockConfig = Field(default_factory=BaostockConfig)
    api_keys: APIKeysConfig = Field(default_factory=APIKeysConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    nacos: NacosConfig = Field(default_factory=NacosConfig)

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
