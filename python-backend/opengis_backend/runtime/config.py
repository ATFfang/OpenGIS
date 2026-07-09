"""Application configuration using Pydantic Settings."""

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """OpenGIS backend configuration."""

    # Server
    host: str = "127.0.0.1"
    port: int = int(os.environ.get("OPENGIS_PORT", "8765"))

    # LLM
    llm_protocol: str = "openai"
    llm_model: str = "gpt-4o"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096

    # Agent
    agent_code_timeout: int = 60
    agent_require_confirmation: bool = True

    # Data
    default_crs: str = "EPSG:4326"
    max_direct_transfer_mb: float = 10.0
    temp_directory: str = ""

    class Config:
        env_prefix = "OPENGIS_"
        env_file = ".env"


settings = Settings()
