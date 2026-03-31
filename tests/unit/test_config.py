"""Tests for configuration loading and validation.

These tests define the contract for the Settings class:
- Loads defaults from config.yaml
- Environment variables override yaml values
- Required secrets cause startup failure if missing
- Invalid values are rejected with clear errors
- Settings is a singleton (loaded once, reused)
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# The imports below will fail until config.py is implemented.
# That is intentional — this is the RED phase of TDD.
# ---------------------------------------------------------------------------
from contextflow.config import Settings, get_settings

# Path to the project-root config.yaml used by these tests.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@pytest.fixture(autouse=True)
def _provide_dummy_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests need a dummy API key because the default provider is 'gemini'.
    Tests that specifically check missing-key behavior override this via patch.dict."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-dummy-key")


class TestLoadsDefaultsFromYaml:
    """Settings should load all default values from config.yaml without any
    environment variable overrides. Every section and every field should be
    present and match the value written in config.yaml."""

    def test_loads_defaults_from_yaml(self) -> None:
        # Arrange — point Settings at the real config.yaml, no env overrides.
        # The autouse fixture provides a dummy GEMINI_API_KEY.
        settings = Settings(_config_path=CONFIG_PATH)

        # Assert — spot-check one value from each section to confirm the full
        # file was loaded. If any section failed to parse, its attribute would
        # be missing or have the wrong default.
        assert settings.ingestion.chunk_size == 500
        assert settings.ingestion.chunk_overlap == 50
        assert settings.ingestion.chunking_strategy == "recursive"
        assert settings.ingestion.supported_formats == ["md", "txt", "pdf"]

        assert settings.embedding.provider == "gemini"
        assert settings.embedding.model == "text-embedding-004"
        assert settings.embedding.dimension == 768
        assert settings.embedding.batch_size == 100

        assert settings.retrieval.top_k == 5
        assert settings.retrieval.similarity_threshold == 0.7
        assert settings.retrieval.fusion_method == "rrf"
        assert settings.retrieval.rrf_k == 60
        assert settings.retrieval.use_reranker is False
        assert settings.retrieval.rerank_top_n == 3

        assert settings.cache.enabled is True
        assert settings.cache.distance_threshold == 0.10
        assert settings.cache.ttl_seconds == 604_800
        assert settings.cache.max_entries == 10_000
        assert settings.cache.require_citations is True

        assert settings.session.max_turns == 100
        assert settings.session.context_window_turns == 10
        assert settings.session.ttl_seconds == 86_400
        assert settings.session.summarize_after_turns == 50

        assert settings.long_term_memory.enabled is True
        assert settings.long_term_memory.ttl_days == 30
        assert settings.long_term_memory.min_confidence == 0.6
        assert settings.long_term_memory.max_facts_injected == 5
        assert settings.long_term_memory.retrieval_threshold == 0.7

        assert settings.llm.provider == "gemini"
        assert settings.llm.model == "gemini-2.0-flash"
        assert settings.llm.base_url is None
        assert settings.llm.max_tokens == 1024
        assert settings.llm.temperature == 0.1
        assert settings.llm.stream is True
        assert settings.llm.timeout_seconds == 30
        assert settings.llm.fallback is None

        assert settings.redis.url == "redis://localhost:6379"
        assert settings.redis.max_connections == 10
        assert settings.redis.index_type == "FLAT"

        assert settings.server.host == "127.0.0.1"
        assert settings.server.port == 8000
        assert settings.server.workers == 1

        assert settings.logging.level == "INFO"
        assert settings.logging.format == "json"

        assert settings.metrics.enabled is True


class TestEnvVarOverridesYaml:
    """Environment variables should take precedence over config.yaml values.
    This is critical for deployment — you set safe defaults in yaml and
    override specific values per environment (staging, production) via env."""

    def test_env_var_overrides_yaml(self) -> None:
        # Arrange — set an env var that should override the yaml default.
        # The naming convention is: CONTEXTFLOW_<SECTION>__<KEY> (double underscore
        # separates nested levels, which is pydantic-settings convention).
        # The autouse fixture already provides GEMINI_API_KEY.
        with patch.dict(os.environ, {"CONTEXTFLOW_REDIS__URL": "redis://prod:6380"}):
            settings = Settings(_config_path=CONFIG_PATH)

        # Assert — env var wins over yaml
        assert settings.redis.url == "redis://prod:6380"


class TestFailsOnMissingRequiredSecret:
    """If the embedding/LLM provider is 'gemini' and GEMINI_API_KEY is not set,
    startup should fail with a clear error — not silently proceed and crash
    later on the first API call with a cryptic auth error."""

    def test_fails_on_missing_required_secret(self) -> None:
        # Arrange — ensure the API key is NOT in the environment.
        env = os.environ.copy()
        env.pop("GEMINI_API_KEY", None)

        with patch.dict(os.environ, env, clear=True):
            # Act & Assert — constructing Settings with provider=gemini and no
            # API key should raise immediately.
            with pytest.raises((ValueError, KeyError)):
                Settings(_config_path=CONFIG_PATH)


class TestValidatesChunkSizeRange:
    """chunk_size must be within 100-1500. Values outside this range should be
    rejected at construction time, not silently accepted and cause subtle
    retrieval quality issues later."""

    def test_rejects_chunk_size_too_small(self) -> None:
        with pytest.raises(ValueError):
            Settings(_config_path=CONFIG_PATH, _overrides={"ingestion": {"chunk_size": 50}})

    def test_rejects_chunk_size_too_large(self) -> None:
        with pytest.raises(ValueError):
            Settings(_config_path=CONFIG_PATH, _overrides={"ingestion": {"chunk_size": 2000}})


class TestValidatesEmbeddingDimensionPositive:
    """Embedding dimension must be a positive integer. Zero or negative would
    cause Redis to reject the FT.CREATE command, but catching it here gives
    a much clearer error message."""

    def test_rejects_zero_dimension(self) -> None:
        with pytest.raises(ValueError):
            Settings(_config_path=CONFIG_PATH, _overrides={"embedding": {"dimension": 0}})

    def test_rejects_negative_dimension(self) -> None:
        with pytest.raises(ValueError):
            Settings(_config_path=CONFIG_PATH, _overrides={"embedding": {"dimension": -1}})


class TestValidatesCacheThresholdRange:
    """cache.distance_threshold must be between 0.01 and 0.30. Outside this
    range: too low means zero cache hits ever (useless), too high means
    returning wrong cached answers (dangerous)."""

    def test_rejects_threshold_too_low(self) -> None:
        with pytest.raises(ValueError):
            Settings(
                _config_path=CONFIG_PATH,
                _overrides={"cache": {"distance_threshold": 0.005}},
            )

    def test_rejects_threshold_too_high(self) -> None:
        with pytest.raises(ValueError):
            Settings(
                _config_path=CONFIG_PATH,
                _overrides={"cache": {"distance_threshold": 0.5}},
            )


class TestValidatesTemperatureRange:
    """LLM temperature must be between 0.0 and 2.0. Negative values are
    meaningless. Above 2.0, most providers return errors or garbage."""

    def test_rejects_negative_temperature(self) -> None:
        with pytest.raises(ValueError):
            Settings(_config_path=CONFIG_PATH, _overrides={"llm": {"temperature": -0.1}})

    def test_rejects_temperature_above_two(self) -> None:
        with pytest.raises(ValueError):
            Settings(_config_path=CONFIG_PATH, _overrides={"llm": {"temperature": 2.5}})


class TestConfigLoadedOnce:
    """get_settings() should return the same object every time it's called.
    Config is loaded once at startup — re-reading on every request would be
    wasteful and could cause inconsistency if the file changes mid-run."""

    def test_config_loaded_once(self) -> None:
        # Arrange — reset singleton so prior tests don't interfere,
        # and provide a dummy API key since default provider is "openai".
        from contextflow.config import reset_settings

        reset_settings()

        # Act — call get_settings() twice (autouse fixture provides GEMINI_API_KEY)
        settings_a = get_settings()
        settings_b = get_settings()

        # Assert — same object in memory (not just equal, actually identical)
        assert settings_a is settings_b

        # Cleanup — reset so this singleton doesn't leak into other tests
        reset_settings()
