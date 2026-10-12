from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")
    bstom_mode: str = "mock"
    llm_provider: Literal["gemini", "compatible", "ollama"] = "gemini"
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    llm_model: str = "gemini-3.5-flash-lite"
    gemini_api_key: str = ""
    llm_api_key: str = ""
    api_max_concurrency: int = Field(4, ge=1, le=4)
    api_requests_per_minute: int = Field(0, ge=0, le=60)
    api_tokens_per_minute: int = Field(0, ge=0, le=1000000)
    session_max_calls: int = Field(0, ge=0, le=1000)
    session_max_messages: int = Field(40, ge=0, le=500)
    session_max_seconds: float = Field(900, ge=0, le=3600)
    max_speech_chars: int = Field(360, ge=30, le=2000)
    max_output_tokens: int = Field(512, ge=64, le=2048)
    reevaluate_seconds: float = Field(12, ge=0.05, le=120)
    speech_cooldown: float = Field(0, ge=0, le=60)
    turn_pause_seconds: float = Field(1, ge=0, le=30)
    mock_chunk_delay: float = Field(0.08, ge=0, le=1)
    ollama_context_length: int = Field(2048, ge=1024, le=32768)
    ollama_fast_mode: bool = True
    ollama_gpu_layers: int = Field(-1, ge=-1, le=999)
    data_dir: str = "data"

    @property
    def active_api_key(self):
        if self.llm_provider == "ollama":
            return ""
        return self.gemini_api_key if self.llm_provider == "gemini" else self.llm_api_key

    @property
    def live_available(self):
        return self.llm_provider == "ollama" or bool(self.active_api_key)

    @property
    def provider_name(self):
        return {"gemini": "Gemini", "compatible": "OpenAI 호환 API", "ollama": "로컬 Ollama"}[self.llm_provider]
