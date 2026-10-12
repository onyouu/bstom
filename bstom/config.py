from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")
    bstom_mode: str = "mock"
    llm_provider: Literal["gemini", "compatible"] = "gemini"
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    llm_model: str = "gemini-3.5-flash-lite"
    gemini_api_key: str = ""
    llm_api_key: str = ""
    api_max_concurrency: int = Field(4, ge=1, le=4)
    api_requests_per_minute: int = Field(0, ge=0, le=60)
    api_tokens_per_minute: int = Field(0, ge=0, le=1000000)
    session_max_calls: int = Field(0, ge=0, le=1000)
    session_max_messages: int = Field(40, ge=1, le=500)
    session_max_seconds: float = Field(900, ge=1, le=3600)
    max_speech_chars: int = Field(360, ge=30, le=2000)
    max_output_tokens: int = Field(512, ge=64, le=2048)
    reevaluate_seconds: float = Field(12, ge=0.05, le=120)
    speech_cooldown: float = Field(0, ge=0, le=60)
    mock_chunk_delay: float = Field(0.08, ge=0, le=1)
    data_dir: str = "data"

    @property
    def active_api_key(self):
        return self.gemini_api_key if self.llm_provider == "gemini" else self.llm_api_key
