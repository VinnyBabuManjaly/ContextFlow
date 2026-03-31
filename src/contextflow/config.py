"""Configuration loader.

Load from config.yaml + environment variables.
Validate all required fields at startup — fail loud if anything is missing.
Expose a single Settings object that the rest of the app imports.

Loading precedence (highest wins):
    1. Environment variables   (CONTEXTFLOW_SECTION__KEY)
    2. _overrides dict         (for testing only)
    3. config.yaml defaults
"""

import os
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, field_validator, model_validator

# ---------------------------------------------------------------------------
# Helper: load YAML file into a plain dict
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file and return its contents as a nested dict."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return data if data is not None else {}


# ---------------------------------------------------------------------------
# Helper: apply CONTEXTFLOW_* environment variable overrides
# ---------------------------------------------------------------------------

def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Scan os.environ for CONTEXTFLOW_SECTION__KEY entries and merge them
    into the config dict. Double-underscore separates nesting levels.

    Example: CONTEXTFLOW_REDIS__URL=redis://prod:6380
             -> data["redis"]["url"] = "redis://prod:6380"
    """
    prefix = "CONTEXTFLOW_"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        # Strip prefix and split on __ to get nested path
        parts = key[len(prefix):].lower().split("__")
        # Walk into the nested dict, creating intermediate dicts as needed
        target = data
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return data


# ---------------------------------------------------------------------------
# Sub-settings: one model per config section
# ---------------------------------------------------------------------------
# Each model defines its fields with defaults matching config.yaml,
# plus validators that enforce the ranges from the config reference doc.
# ---------------------------------------------------------------------------

class IngestionSettings(BaseModel):
    chunk_size: int = 500
    chunk_overlap: int = 50
    chunking_strategy: str = "recursive"
    supported_formats: list[str] = ["md", "txt", "pdf"]

    @field_validator("chunk_size")
    @classmethod
    def chunk_size_in_range(cls, v: int) -> int:
        if not 100 <= v <= 1500:
            raise ValueError(f"chunk_size must be between 100 and 1500, got {v}")
        return v

    @field_validator("chunk_overlap")
    @classmethod
    def chunk_overlap_in_range(cls, v: int) -> int:
        if not 0 <= v <= 200:
            raise ValueError(f"chunk_overlap must be between 0 and 200, got {v}")
        return v

    @field_validator("chunking_strategy")
    @classmethod
    def valid_chunking_strategy(cls, v: str) -> str:
        allowed = {"recursive", "fixed", "heading"}
        if v not in allowed:
            raise ValueError(f"chunking_strategy must be one of {allowed}, got '{v}'")
        return v


class EmbeddingSettings(BaseModel):
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    dimension: int = 1536
    batch_size: int = 100

    @field_validator("dimension")
    @classmethod
    def dimension_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"embedding dimension must be positive, got {v}")
        return v

    @field_validator("batch_size")
    @classmethod
    def batch_size_in_range(cls, v: int) -> int:
        if not 1 <= v <= 2048:
            raise ValueError(f"batch_size must be between 1 and 2048, got {v}")
        return v

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, v: str) -> str:
        allowed = {"gemini", "openai", "local"}
        if v not in allowed:
            raise ValueError(f"embedding provider must be one of {allowed}, got '{v}'")
        return v


class RetrievalSettings(BaseModel):
    top_k: int = 5
    similarity_threshold: float = 0.7
    fusion_method: str = "rrf"
    rrf_k: int = 60
    use_reranker: bool = False
    rerank_top_n: int = 3

    @field_validator("top_k")
    @classmethod
    def top_k_in_range(cls, v: int) -> int:
        if not 1 <= v <= 20:
            raise ValueError(f"top_k must be between 1 and 20, got {v}")
        return v

    @field_validator("similarity_threshold")
    @classmethod
    def similarity_threshold_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"similarity_threshold must be between 0.0 and 1.0, got {v}")
        return v

    @field_validator("fusion_method")
    @classmethod
    def valid_fusion_method(cls, v: str) -> str:
        allowed = {"rrf", "weighted"}
        if v not in allowed:
            raise ValueError(f"fusion_method must be one of {allowed}, got '{v}'")
        return v

    @field_validator("rrf_k")
    @classmethod
    def rrf_k_in_range(cls, v: int) -> int:
        if not 1 <= v <= 100:
            raise ValueError(f"rrf_k must be between 1 and 100, got {v}")
        return v

    @field_validator("rerank_top_n")
    @classmethod
    def rerank_top_n_in_range(cls, v: int) -> int:
        if not 1 <= v <= 10:
            raise ValueError(f"rerank_top_n must be between 1 and 10, got {v}")
        return v


class CacheSettings(BaseModel):
    enabled: bool = True
    distance_threshold: float = 0.10
    ttl_seconds: int = 604_800
    max_entries: int = 10_000
    require_citations: bool = True

    @field_validator("distance_threshold")
    @classmethod
    def distance_threshold_in_range(cls, v: float) -> float:
        if not 0.01 <= v <= 0.30:
            raise ValueError(f"distance_threshold must be between 0.01 and 0.30, got {v}")
        return v

    @field_validator("max_entries")
    @classmethod
    def max_entries_in_range(cls, v: int) -> int:
        if not 100 <= v <= 100_000:
            raise ValueError(f"max_entries must be between 100 and 100000, got {v}")
        return v


class SessionSettings(BaseModel):
    max_turns: int = 100
    context_window_turns: int = 10
    ttl_seconds: int = 86_400
    summarize_after_turns: int = 50

    @field_validator("max_turns")
    @classmethod
    def max_turns_in_range(cls, v: int) -> int:
        if not 10 <= v <= 1000:
            raise ValueError(f"max_turns must be between 10 and 1000, got {v}")
        return v

    @field_validator("context_window_turns")
    @classmethod
    def context_window_turns_in_range(cls, v: int) -> int:
        if not 1 <= v <= 50:
            raise ValueError(f"context_window_turns must be between 1 and 50, got {v}")
        return v

    @field_validator("summarize_after_turns")
    @classmethod
    def summarize_after_turns_in_range(cls, v: int) -> int:
        if not 20 <= v <= 200:
            raise ValueError(f"summarize_after_turns must be between 20 and 200, got {v}")
        return v


class LongTermMemorySettings(BaseModel):
    enabled: bool = True
    ttl_days: int = 30
    min_confidence: float = 0.6
    max_facts_injected: int = 5
    retrieval_threshold: float = 0.7

    @field_validator("ttl_days")
    @classmethod
    def ttl_days_in_range(cls, v: int) -> int:
        if not 7 <= v <= 365:
            raise ValueError(f"ttl_days must be between 7 and 365, got {v}")
        return v

    @field_validator("min_confidence")
    @classmethod
    def min_confidence_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"min_confidence must be between 0.0 and 1.0, got {v}")
        return v

    @field_validator("max_facts_injected")
    @classmethod
    def max_facts_injected_in_range(cls, v: int) -> int:
        if not 1 <= v <= 20:
            raise ValueError(f"max_facts_injected must be between 1 and 20, got {v}")
        return v

    @field_validator("retrieval_threshold")
    @classmethod
    def retrieval_threshold_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"retrieval_threshold must be between 0.0 and 1.0, got {v}")
        return v


class FallbackSettings(BaseModel):
    """Optional fallback LLM configuration — used when primary provider fails."""
    provider: str
    model: str


class LLMSettings(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    base_url: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.1
    stream: bool = True
    timeout_seconds: int = 30
    fallback: FallbackSettings | None = None

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, v: str) -> str:
        allowed = {"openai", "gemini", "ollama"}
        if v not in allowed:
            raise ValueError(f"llm provider must be one of {allowed}, got '{v}'")
        return v

    @field_validator("temperature")
    @classmethod
    def temperature_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError(f"temperature must be between 0.0 and 2.0, got {v}")
        return v

    @field_validator("max_tokens")
    @classmethod
    def max_tokens_in_range(cls, v: int) -> int:
        if not 100 <= v <= 4096:
            raise ValueError(f"max_tokens must be between 100 and 4096, got {v}")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def timeout_in_range(cls, v: int) -> int:
        if not 5 <= v <= 120:
            raise ValueError(f"timeout_seconds must be between 5 and 120, got {v}")
        return v


class RedisSettings(BaseModel):
    url: str = "redis://localhost:6379"
    max_connections: int = 10
    index_type: str = "FLAT"
    hnsw_m: int = 16
    hnsw_ef_construction: int = 200
    hnsw_ef_runtime: int = 10

    @field_validator("index_type")
    @classmethod
    def valid_index_type(cls, v: str) -> str:
        allowed = {"FLAT", "HNSW"}
        if v not in allowed:
            raise ValueError(f"index_type must be one of {allowed}, got '{v}'")
        return v

    @field_validator("max_connections")
    @classmethod
    def max_connections_in_range(cls, v: int) -> int:
        if not 5 <= v <= 50:
            raise ValueError(f"max_connections must be between 5 and 50, got {v}")
        return v


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = 1

    @field_validator("port")
    @classmethod
    def port_in_range(cls, v: int) -> int:
        if not 1024 <= v <= 65535:
            raise ValueError(f"port must be between 1024 and 65535, got {v}")
        return v

    @field_validator("workers")
    @classmethod
    def workers_in_range(cls, v: int) -> int:
        if not 1 <= v <= 4:
            raise ValueError(f"workers must be between 1 and 4, got {v}")
        return v


class LoggingSettings(BaseModel):
    level: str = "INFO"
    format: str = "json"

    @field_validator("level")
    @classmethod
    def valid_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR"}
        if v not in allowed:
            raise ValueError(f"logging level must be one of {allowed}, got '{v}'")
        return v

    @field_validator("format")
    @classmethod
    def valid_format(cls, v: str) -> str:
        allowed = {"json", "text"}
        if v not in allowed:
            raise ValueError(f"logging format must be one of {allowed}, got '{v}'")
        return v


class MetricsSettings(BaseModel):
    enabled: bool = True


# ---------------------------------------------------------------------------
# Root Settings: assembles all sections, loads from YAML + env
# ---------------------------------------------------------------------------

class Settings(BaseModel):
    """Root configuration object.

    Construction:
        Settings(_config_path=Path("config.yaml"))           # from YAML
        Settings(_config_path=path, _overrides={...})         # YAML + overrides (testing)

    Loading order:
        1. Read config.yaml into a dict
        2. Merge _overrides on top (if provided)
        3. Merge CONTEXTFLOW_* environment variables on top
        4. Pass merged dict to Pydantic for validation
    """

    ingestion: IngestionSettings = IngestionSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    retrieval: RetrievalSettings = RetrievalSettings()
    cache: CacheSettings = CacheSettings()
    session: SessionSettings = SessionSettings()
    long_term_memory: LongTermMemorySettings = LongTermMemorySettings()
    llm: LLMSettings = LLMSettings()
    redis: RedisSettings = RedisSettings()
    server: ServerSettings = ServerSettings()
    logging: LoggingSettings = LoggingSettings()
    metrics: MetricsSettings = MetricsSettings()

    def __init__(
        self,
        _config_path: Path | None = None,
        _overrides: dict[str, Any] | None = None,
        **kwargs: object,
    ) -> None:
        # Step 1: Load YAML defaults
        data: dict[str, Any] = {}
        if _config_path is not None:
            data = _load_yaml(_config_path)

        # Step 2: Merge test overrides (shallow per-section merge)
        if _overrides is not None:
            for section, values in _overrides.items():
                if section in data and isinstance(data[section], dict):
                    data[section].update(values)
                else:
                    data[section] = values

        # Step 3: Merge environment variable overrides (highest precedence)
        data = _apply_env_overrides(data)

        # Step 4: Pass to Pydantic for parsing and validation
        data.update(kwargs)
        super().__init__(**data)

    @model_validator(mode="after")
    def check_required_secrets(self) -> "Settings":
        """If a cloud provider is configured, its API key must be present.
        Fail at startup, not on the first API call 10 minutes later."""
        providers = {self.embedding.provider, self.llm.provider}

        if "openai" in providers and not os.environ.get("OPENAI_API_KEY"):
            raise ValueError(
                "OPENAI_API_KEY environment variable is required when "
                "embedding.provider or llm.provider is set to 'openai'. "
                "Set it in your environment or .env file."
            )

        if "gemini" in providers and not os.environ.get("GEMINI_API_KEY"):
            raise ValueError(
                "GEMINI_API_KEY environment variable is required when "
                "embedding.provider or llm.provider is set to 'gemini'. "
                "Set it in your environment or .env file."
            )
        return self


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """Return the global Settings singleton.

    Loads from config.yaml on first call. Subsequent calls return the same
    object — config is read once at startup, not per-request.
    """
    global _settings_instance
    if _settings_instance is None:
        # Look for config.yaml in the current working directory (project root)
        config_path = Path.cwd() / "config.yaml"
        _settings_instance = Settings(_config_path=config_path)
    return _settings_instance


def reset_settings() -> None:
    """Clear the singleton — used by tests to avoid state leaking between tests."""
    global _settings_instance
    _settings_instance = None
