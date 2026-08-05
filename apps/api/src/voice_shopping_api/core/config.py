from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, computed_field
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
        "postgresql+asyncpg://postgres:postgres@localhost:5432/voice-shopping-agents"
    )
    redis_url: str = "redis://localhost:6379/0"
    taxonomy_cache_ttl_seconds: int = Field(default=900, ge=1)
    langgraph_checkpoint_enabled: bool = True
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
