"""Environment-backed configuration with lazy external clients."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    python_minimum: str = "3.11"
    elastic_server: str | None = os.getenv("ELASTIC_SERVER")
    elastic_user: str | None = os.getenv("ELASTIC_USER")
    elastic_password: str | None = os.getenv("ELASTIC_PASSWORD")
    elastic_verify_certificates: bool = _bool("ELASTIC_VERIFY_CERTIFICATES", True)
    default_index: str | None = os.getenv("ELASTIC_INDEX")
    chart_group_field: str | None = os.getenv("CHART_GROUP_FIELD")
    timezone: str = os.getenv("APP_TIMEZONE", "Asia/Kolkata")
    chart_output_dir: Path = Path(os.getenv("CHART_OUTPUT_DIR", "artifacts/charts"))
    audit_log_path: Path = Path(os.getenv("AUDIT_LOG_PATH", "artifacts/audit.jsonl"))
    elastic_index_data_from: int = int(os.getenv("ELASTIC_INDEX_DATA_FROM", "0"))
    elastic_index_data_size: int = int(os.getenv("ELASTIC_INDEX_DATA_SIZE", "5"))
    elastic_index_data_max_size: int = int(os.getenv("ELASTIC_INDEX_DATA_MAX_SIZE", "50"))
    token_limit: int = int(os.getenv("TOKEN_LIMIT", "6000"))
    max_search_retries: int = int(os.getenv("MAX_SEARCH_RETRIES", "3"))
    llm_provider: str = os.getenv("LLM_PROVIDER", "sarvam")
    llm_model: str = os.getenv("SARVAM_MODEL", os.getenv("OPENAI_MODEL", "sarvam-m"))
    sarvam_api_key: str | None = os.getenv("SARVAM_API_KEY")
    sarvam_base_url: str = os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai/v1")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_base_url: str | None = os.getenv("OPENAI_BASE_URL")

    def validate(self) -> list[str]:
        missing = []
        if not self.elastic_server:
            missing.append("ELASTIC_SERVER")
        if self.llm_provider.lower() == "sarvam" and not self.sarvam_api_key:
            missing.append("SARVAM_API_KEY")
        if self.llm_provider.lower() == "openai" and not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        return missing

    def validate_elasticsearch(self) -> None:
        if not self.elastic_server:
            raise ValueError("Missing required Elasticsearch setting: ELASTIC_SERVER")
        if _is_placeholder(self.elastic_server):
            raise ValueError("ELASTIC_SERVER still contains a placeholder value")
        if _is_placeholder(self.elastic_user) or _is_placeholder(self.elastic_password):
            raise ValueError("Elasticsearch credentials still contain placeholder values")

    @cached_property
    def _elasticsearch_client(self):
        self.validate_elasticsearch()
        from elasticsearch import Elasticsearch
        auth = (self.elastic_user, self.elastic_password) if self.elastic_user else None
        return Elasticsearch(self.elastic_server, basic_auth=auth, verify_certs=self.elastic_verify_certificates)

    def elasticsearch(self):
        """Return the configured, cached Elasticsearch client."""
        return self._elasticsearch_client

    def llm(self):
        from langchain_openai import ChatOpenAI
        sarvam = self.llm_provider.lower() == "sarvam"
        return ChatOpenAI(
            api_key=self.sarvam_api_key if sarvam else self.openai_api_key,
            base_url=self.sarvam_base_url if sarvam else self.openai_base_url,
            model=self.llm_model if sarvam else self.openai_model,
            temperature=0,
        )


cfg = Settings()


def _is_placeholder(value: str | None) -> bool:
    return bool(value and value.strip().startswith("<") and value.strip().endswith(">"))


def validate_index_name(index_name: str) -> str:
    value = index_name.strip()
    if not value or _is_placeholder(value):
        raise ValueError("A real Elasticsearch index name is required")
    return value


def validate_field_name(field_name: str | None) -> str:
    value = (field_name or "").strip()
    if not value or _is_placeholder(value):
        raise ValueError("CHART_GROUP_FIELD must be set to a real mapped field")
    return value
