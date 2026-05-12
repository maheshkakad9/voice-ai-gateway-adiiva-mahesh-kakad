from __future__ import annotations
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: str = Field("development", alias="APP_ENV")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # JWT
    jwt_secret_key: str = Field(..., alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field("HS256", alias="JWT_ALGORITHM")
    jwt_expiry_minutes: int = Field(60, alias="JWT_EXPIRY_MINUTES")

    # Redis
    redis_url: str = Field("redis://redis:6379/0", alias="REDIS_URL")

    # Rate limiting
    max_concurrent_calls_per_user: int = Field(2, alias="MAX_CONCURRENT_CALLS_PER_USER")

    # AI services
    google_api_key: str | None = Field(None, alias="GOOGLE_API_KEY")
    deepgram_api_key: str = Field(..., alias="DEEPGRAM_API_KEY")
    cartesia_api_key: str = Field(..., alias="CARTESIA_API_KEY")
    openai_api_key: str | None = Field(None, alias="OPENAI_API_KEY")
    groq_api_key: str = Field(..., alias="GROQ_API_KEY")

    # Model config
    gemini_model: str = Field("gemini-2.0-flash", alias="GEMINI_MODEL")
    openai_model: str = Field("gpt-4o-mini", alias="OPENAI_MODEL")
    groq_model: str = Field("llama-3.3-70b-versatile", alias="GROQ_MODEL")
    cartesia_voice_id: str = Field("71a7ad14-091c-4e8e-a314-022ece01c121", alias="CARTESIA_VOICE_ID")

    # Cost rates
    groq_input_cost_per_1k_tokens: float = Field(0.0, alias="GROQ_INPUT_COST_PER_1K_TOKENS")
    groq_output_cost_per_1k_tokens: float = Field(0.0, alias="GROQ_OUTPUT_COST_PER_1K_TOKENS")
    openai_input_cost_per_1k_tokens: float = Field(0.00015, alias="OPENAI_INPUT_COST_PER_1K_TOKENS")
    openai_output_cost_per_1k_tokens: float = Field(0.0006, alias="OPENAI_OUTPUT_COST_PER_1K_TOKENS")
    gemini_input_cost_per_1k_tokens: float = Field(0.000075, alias="GEMINI_INPUT_COST_PER_1K_TOKENS")
    gemini_output_cost_per_1k_tokens: float = Field(0.0003, alias="GEMINI_OUTPUT_COST_PER_1K_TOKENS")
    deepgram_cost_per_second: float = Field(0.0001, alias="DEEPGRAM_COST_PER_SECOND")
    cartesia_cost_per_1k_chars: float = Field(0.09, alias="CARTESIA_COST_PER_1K_CHARS")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
