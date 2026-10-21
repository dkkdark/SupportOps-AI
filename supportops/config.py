from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    database_url: str = "sqlite:///./var/supportops.db"
    orders_api_url: str = "http://127.0.0.1:8001"
    orders_timeout_seconds: float = 2.0
    model_provider: str = "baseline"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_api_key: str = ""
    app_api_key: str = ""
    fault_injection_enabled: bool = False
    otel_service_name: str = "supportops-api"
    otel_exporter_otlp_endpoint: str = ""
    code_version: str = "local"
    model_input_usd_per_million: float = 0
    model_output_usd_per_million: float = 0


@lru_cache
def settings() -> Settings:
    return Settings()
