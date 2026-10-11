from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")
    bstom_mode: str = "mock"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "qwen/qwen3.8-27b"
    llm_api_key: str = ""
    api_max_concurrency: int = Field(2, ge=1, le=4)
    api_requests_per_minute: int = Field(12, ge=1, le=60)
    api_tokens_per_minute: int = Field(6500, ge=1000, le=1000000)
    session_max_calls: int = Field(60, ge=1, le=1000)
    session_max_messages: int = Field(40, ge=1, le=500)
    session_max_seconds: float = Field(900, ge=1, le=3600)
    max_speech_chars: int = Field(360, ge=30, le=2000)
    max_output_tokens: int = Field(512, ge=64, le=2048)
    reevaluate_seconds: float = Field(12, ge=0.05, le=120)
    speech_cooldown: float = Field(3, ge=0, le=60)
    mock_chunk_delay: float = Field(0.08, ge=0, le=1)
    data_dir: str = "data"
