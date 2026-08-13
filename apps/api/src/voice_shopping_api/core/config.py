from functools import lru_cache
from pathlib import Path
from secrets import token_urlsafe

from pydantic import AliasChoices, Field, PrivateAttr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ENV_FILE = Path(__file__).resolve().parents[5] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ENV_FILE,
        env_prefix="VOICE_SHOPPING_",
        extra="ignore",
    )

    app_name: str = "Voice Shopping API"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/voice_shopping_agents"
    )
    redis_url: str = "redis://localhost:6379/0"
    taxonomy_cache_ttl_seconds: int = Field(default=900, ge=1)
    catalog_cache_enabled: bool = True
    catalog_cache_ttl_seconds: int = Field(default=60, ge=1, le=3600)
    catalog_cache_redis_url: str = ""
    product_embedding_cache_ttl_seconds: int = 30 * 24 * 60 * 60
    langgraph_checkpoint_enabled: bool = True
    langgraph_checkpoint_init_timeout_seconds: float = Field(
        default=5.0, ge=0.1, le=60.0
    )
    langgraph_checkpoint_database_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "LANGGRAPH_CHECKPOINT_DATABASE_URL",
            "VOICE_SHOPPING_LANGGRAPH_CHECKPOINT_DATABASE_URL",
        ),
    )
    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:5174,http://localhost:5175"
    )
    log_level: str = "INFO"
    jwt_secret: str = ""
    jwt_issuer: str = "voice-shopping-api"
    jwt_audience: str = "voice-shopping-web"
    jwt_access_token_ttl_minutes: int = Field(default=120, ge=5, le=1_440)
    dashscope_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DASHSCOPE_API_KEY", "VOICE_SHOPPING_DASHSCOPE_API_KEY"),
    )
    dashscope_chat_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_http_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    agent_model: str = "qwen3.7-flash"
    embedding_model: str = "qwen3.7-text-embedding"
    reranker_model: str = "qwen3-rerank"
    asr_model: str = "qwen-audio-3.0-asr-flash-streaming"
    tts_model: str = "qwen-audio-3.0-tts-plus"
    tts_voice: str = "longanlingxin"
    langsmith_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LANGSMITH_API_KEY", "VOICE_SHOPPING_LANGSMITH_API_KEY"),
    )
    langsmith_project: str = Field(
        default="voice-shopping-agents",
        validation_alias=AliasChoices("LANGSMITH_PROJECT", "VOICE_SHOPPING_LANGSMITH_PROJECT"),
    )
    _development_jwt_secret: str = PrivateAttr(default_factory=lambda: token_urlsafe(48))

    @property
    def jwt_signing_key(self) -> str:
        if self.jwt_secret:
            return self.jwt_secret
        if self.environment.lower() in {"development", "test"}:
            return self._development_jwt_secret
        raise RuntimeError("VOICE_SHOPPING_JWT_SECRET must be configured outside development")

    @computed_field
    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @computed_field
    @property
    def langgraph_checkpoint_url(self) -> str:
        if self.langgraph_checkpoint_database_url:
            return self.langgraph_checkpoint_database_url
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
