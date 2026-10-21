from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator, Field
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    session_days: int = 7
    allow_demo_signup: bool = False
    database_url: str = "sqlite:///./var/supportops.db"
    db_host: str = ""
    db_name: str = "supportops"
    db_user: str = "supportops"
    db_password: str = ""
    orders_api_url: str = "http://127.0.0.1:8001"
    orders_timeout_seconds: float = 2.0
    model_provider: str = "openai"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    agent_model: str = "gpt-5.4-mini"
    verifier_model: str = "gpt-5.4-mini"
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-large"
    agent_max_turns: int = Field(default=6, ge=2, le=10)
    agent_max_tools: int = Field(default=8, ge=1, le=20)
    agent_timeout_seconds: float = Field(default=120, ge=10, le=240)
    agent_max_output_tokens: int = Field(default=3000, ge=500, le=6000)
    agent_run_credit_reserve: int = Field(default=90, ge=10, le=500)
    agent_provider_cost_limit: float = Field(default=0.40, gt=0, le=5)
    stripe_scale_price_id: str = ""
    agent_monthly_limit: int = Field(default=100, ge=1)
    request_monthly_limit: int = Field(default=1000, ge=1)
    public_base_url: str = "http://127.0.0.1:8010"
    turnstile_site_key: str = ""
    turnstile_secret_key: str = ""
    public_intake_enabled: bool = True
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_starter_price_id: str = ""
    stripe_growth_price_id: str = ""
    legal_business_name: str = ""
    legal_contact_email: str = ""
    legal_terms_url: str = ""
    legal_privacy_url: str = ""
    legal_documents_approved: bool = False
    stripe_automatic_tax: bool = False
    require_verified_email: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_api_key: str = ""
    fault_injection_enabled: bool = False
    otel_service_name: str = "supportops-api"
    otel_exporter_otlp_endpoint: str = ""
    code_version: str = "local"
    verifier_input_usd_per_million: float = 0.75
    verifier_output_usd_per_million: float = 4.50
    embedding_input_usd_per_million: float = 0.13
    model_input_usd_per_million: float = 0.75
    model_output_usd_per_million: float = 4.50

    @model_validator(mode="after")
    def database_from_secret(self):
        if (
            self.model_provider == "openai"
            and min(
                self.model_input_usd_per_million,
                self.model_output_usd_per_million,
                self.verifier_input_usd_per_million,
                self.verifier_output_usd_per_million,
            )
            <= 0
        ):
            raise ValueError(
                "Configure positive model token rates for metered OpenAI usage"
            )
        if (
            self.embedding_provider == "openai"
            and self.embedding_input_usd_per_million <= 0
        ):
            raise ValueError("Configure a positive embedding token rate")
        if self.agent_provider_cost_limit > self.agent_run_credit_reserve * 0.005:
            raise ValueError(
                "Agent cost budget cannot exceed its maximum reserved AI credits"
            )
        if self.db_host:
            if not self.db_password:
                raise ValueError("DB_PASSWORD is required with DB_HOST")
            self.database_url = URL.create(
                "postgresql+psycopg",
                username=self.db_user,
                password=self.db_password,
                host=self.db_host,
                port=5432,
                database=self.db_name,
                query={"sslmode": "require"},
            ).render_as_string(hide_password=False)
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
