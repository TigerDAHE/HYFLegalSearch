from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    debug: bool = Field(default=True, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    llm_model: str = Field(default="openai/gpt-4o-mini", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.1, alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=1800, alias="LLM_MAX_TOKENS")
    llm_timeout_seconds: int = Field(default=45, alias="LLM_TIMEOUT_SECONDS")
    llm_api_base: str | None = Field(default=None, alias="LLM_API_BASE")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")

    serper_api_key: str | None = Field(default=None, alias="SERPER_API_KEY")
    serper_endpoint: str = Field(default="https://google.serper.dev/search", alias="SERPER_ENDPOINT")
    serper_num_results: int = Field(default=5, alias="SERPER_NUM_RESULTS")
    serper_timeout_seconds: float = Field(default=20, alias="SERPER_TIMEOUT_SECONDS")
    serper_retries: int = Field(default=2, alias="SERPER_RETRIES")
    serper_retry_backoff_seconds: float = Field(default=0.8, alias="SERPER_RETRY_BACKOFF_SECONDS")
    serper_trust_env: bool = Field(default=True, alias="SERPER_TRUST_ENV")
    serper_max_query_length: int = Field(default=180, alias="SERPER_MAX_QUERY_LENGTH")

    search_complexity_threshold: int = Field(default=3, alias="SEARCH_COMPLEXITY_THRESHOLD")
    search_confidence_threshold: float = Field(default=0.65, alias="SEARCH_CONFIDENCE_THRESHOLD")

    fetch_timeout_seconds: int = Field(default=12, alias="FETCH_TIMEOUT_SECONDS")
    max_sources_for_synthesis: int = Field(default=6, alias="MAX_SOURCES_FOR_SYNTHESIS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
