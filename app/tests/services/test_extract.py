"""Tests for ADRExtractor: constraint extraction from ADR documents.

Public interface under test:
    LangExtractConfig: config dataclass (model_id, model_url, api_key_env)
    ADRExtractor: extracts ConstraintEdge objects from ADR text
        - extract_constraints(adr_text, adr_id, adr_path) -> ExtractionResult
        - extract_from_file(adr_path) -> ExtractionResult
        - extract_from_directory(adr_dir) -> list[ExtractionResult]

All tests mock langextract.extract() to avoid real LLM calls.
Integration tests with real LLM calls are in test_langextract_eval.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.models import ConstraintEdge, ExtractionError, ExtractionResult, PredicateType


# ---------------------------------------------------------------------------
# Fixtures: mock langextract responses
# ---------------------------------------------------------------------------


def _make_extraction(
    subject: str = "services.*",
    predicate: str = "prohibits_dependency",
    object_: str = "db.mysql",
    justification: str = "Direct MySQL connections are prohibited.",
    char_start: int = 45,
    char_end: int = 120,
) -> MagicMock:
    """Create a mock langextract Extraction object."""
    extraction = MagicMock()
    extraction.extraction_class = "adr_constraint"
    extraction.extraction_text = f"{subject} {predicate} {object_}"
    extraction.attributes = {
        "subject": subject,
        "predicate": predicate,
        "object": object_,
        "justification": justification,
    }
    # char_interval is an object with start_pos and end_pos
    char_interval = MagicMock()
    char_interval.start_pos = char_start
    char_interval.end_pos = char_end
    extraction.char_interval = char_interval
    return extraction


def _make_extraction_no_char_interval(
    subject: str = "services.*",
    predicate: str = "prohibits_dependency",
    object_: str = "db.mysql",
    justification: str = "No grounding.",
) -> MagicMock:
    """Create a mock extraction without char_interval (should be skipped)."""
    extraction = MagicMock()
    extraction.extraction_class = "adr_constraint"
    extraction.attributes = {
        "subject": subject,
        "predicate": predicate,
        "object": object_,
        "justification": justification,
    }
    extraction.char_interval = None
    return extraction


def _make_langextract_result(extractions: list[MagicMock]) -> MagicMock:
    """Create a mock langextract extraction result with a list of extractions."""
    result = MagicMock()
    result.extractions = extractions
    return result


# ---------------------------------------------------------------------------
# Sample ADR text
# ---------------------------------------------------------------------------

ADR_001_TEXT = """\
# ADR-001: MySQL Storage

## Status: Accepted

## Decision

The database.query module is the only permitted interface for database
access. All services must route queries through this interface. Direct MySQL
connections are prohibited for services in the services namespace.
"""

ADR_NO_CONSTRAINTS_TEXT = """\
# ADR-006: Code Style

## Status: Accepted

## Decision

We will use Black for code formatting and isort for import sorting.

No architectural constraints apply.
"""


# ===========================================================================
# 1. LangExtractConfig
# ===========================================================================


class TestLangExtractConfig:
    """LangExtractConfig holds model and API configuration."""

    def test_default_values(self) -> None:
        from services.adr_extract import LangExtractConfig

        config = LangExtractConfig()
        assert config.model_id is not None
        assert config.model_url is not None
        assert config.api_key_env == "OPENROUTER_API_KEY"
        assert config.temperature == 0.0
        assert config.provider == "openai"

    def test_custom_values(self) -> None:
        from services.adr_extract import LangExtractConfig

        config = LangExtractConfig(
            model_id="google/gemini-3.1-flash-lite",
            model_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
            provider="openai",
        )
        assert config.model_id == "google/gemini-3.1-flash-lite"
        assert config.model_url == "https://openrouter.ai/api/v1"
        assert config.api_key_env == "OPENROUTER_API_KEY"
        assert config.provider == "openai"

    def test_default_provider_is_openai(self) -> None:
        from services.adr_extract import LangExtractConfig

        config = LangExtractConfig()
        assert config.provider == "openai"

    def test_custom_provider(self) -> None:
        from services.adr_extract import LangExtractConfig

        config = LangExtractConfig(provider="ollama")
        assert config.provider == "ollama"

    def test_from_repos_yaml(self) -> None:
        """Config can be loaded from repos.yaml langextract section."""
        from services.adr_extract import LangExtractConfig

        yaml_config = {
            "model_id": "google/gemini-3.1-flash-lite",
            "model_url": "https://openrouter.ai/api/v1",
            "api_key_env": "OPENROUTER_API_KEY",
            "provider": "openai",
        }
        config = LangExtractConfig.from_dict(yaml_config)
        assert config.model_id == "google/gemini-3.1-flash-lite"
        assert config.model_url == "https://openrouter.ai/api/v1"
        assert config.provider == "openai"

    def test_from_dict_default_api_key_env_is_openrouter(self) -> None:
        """from_dict defaults api_key_env to OPENROUTER_API_KEY, not OLLAMA_API_KEY."""
        from services.adr_extract import LangExtractConfig

        config = LangExtractConfig.from_dict({})
        assert config.api_key_env == "OPENROUTER_API_KEY"

    def test_from_dict_temperature_parsing(self) -> None:
        """from_dict parses temperature from YAML config."""
        from services.adr_extract import LangExtractConfig

        config = LangExtractConfig.from_dict({"temperature": 0.7})
        assert config.temperature == 0.7

    def test_from_dict_default_temperature(self) -> None:
        """from_dict defaults temperature to 0.0 when not specified."""
        from services.adr_extract import LangExtractConfig

        config = LangExtractConfig.from_dict({})
        assert config.temperature == 0.0

    def test_env_vars_read_at_construction_time(self) -> None:
        """LangExtractConfig reads env vars when instantiated, not at class definition."""
        from services.adr_extract import LangExtractConfig

        key = "LANGEXTRACT_MODEL_ID"
        original = os.environ.get(key)
        try:
            if key in os.environ:
                del os.environ[key]
            config_before = LangExtractConfig()
            assert config_before.model_id == "google/gemini-3.1-flash-lite"

            os.environ[key] = "post-import-model"
            config_after = LangExtractConfig()
            assert config_after.model_id == "post-import-model"
        finally:
            if original is not None:
                os.environ[key] = original
            elif key in os.environ:
                del os.environ[key]


# ===========================================================================
# 2. ADRExtractor.extract_constraints: happy path
# ===========================================================================


class TestExtractConstraintsHappyPath:
    """Extract constraints from ADR text using mocked langextract."""

    @patch("services.adr_extract.lx.extract")
    def test_single_constraint(self, mock_extract: MagicMock) -> None:
        """One valid extraction produces one ConstraintEdge."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        mock_extract.return_value = _make_langextract_result(
            [_make_extraction()]
        )

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        extractor = ADRExtractor(config=config)
        result = extractor.extract_constraints(
            adr_text=ADR_001_TEXT,
            adr_id="ADR-001",
            adr_path="docs/adr/ADR-001-mysql-storage.md",
        )

        assert len(result.constraints) == 1
        assert result.constraints[0].subject == "services.*"
        assert result.constraints[0].predicate is PredicateType.PROHIBITS_DEPENDENCY
        assert result.constraints[0].object == "db.mysql"
        assert result.constraints[0].adr_id == "ADR-001"
        assert result.errors == []

    @patch("services.adr_extract.lx.extract")
    def test_multiple_constraints(self, mock_extract: MagicMock) -> None:
        """Multiple extractions produce multiple ConstraintEdges."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        mock_extract.return_value = _make_langextract_result(
            [
                _make_extraction(
                    subject="services.*",
                    predicate="prohibits_dependency",
                    object_="db.mysql",
                    justification="Direct MySQL connections prohibited.",
                    char_start=45,
                    char_end=120,
                ),
                _make_extraction(
                    subject="services.*",
                    predicate="requires_implementation",
                    object_="db.query",
                    justification="All services must route queries through this interface.",
                    char_start=10,
                    char_end=80,
                ),
            ]
        )

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        extractor = ADRExtractor(config=config)
        result = extractor.extract_constraints(
            adr_text=ADR_001_TEXT,
            adr_id="ADR-001",
            adr_path="docs/adr/ADR-001-mysql-storage.md",
        )

        assert len(result.constraints) == 2
        predicates = {c.predicate for c in result.constraints}
        assert PredicateType.PROHIBITS_DEPENDENCY in predicates
        assert PredicateType.REQUIRES_IMPLEMENTATION in predicates

    @patch("services.adr_extract.lx.extract")
    def test_extractions_passed_to_langextract(self, mock_extract: MagicMock) -> None:
        """ADRExtractor passes prompt_description and examples to langextract."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        mock_extract.return_value = _make_langextract_result([])

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        extractor = ADRExtractor(config=config)
        extractor.extract_constraints(
            adr_text="Some text",
            adr_id="ADR-001",
            adr_path="docs/adr/ADR-001.md",
        )

        mock_extract.assert_called_once()
        call_kwargs = mock_extract.call_args
        assert "prompt_description" in call_kwargs.kwargs or len(call_kwargs.args) > 0


class TestExtractConstraintsConfigRouting:
    """ADRExtractor uses factory.ModelConfig with explicit provider for OpenRouter."""

    @patch("services.adr_extract.lx.extract")
    def test_config_parameter_uses_factory_model_config(self, mock_extract: MagicMock) -> None:
        """extract_constraints passes config=factory.ModelConfig to lx.extract."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        os.environ["TEST_API_KEY"] = "test-key-123"
        try:
            mock_extract.return_value = _make_langextract_result([_make_extraction()])

            config = LangExtractConfig(
                model_id="google/gemini-3.1-flash-lite",
                model_url="https://openrouter.ai/api/v1",
                api_key_env="TEST_API_KEY",
                provider="openai",
            )
            extractor = ADRExtractor(config=config)
            extractor.extract_constraints(
                adr_text=ADR_001_TEXT,
                adr_id="ADR-001",
                adr_path="docs/adr/ADR-001-mysql-storage.md",
            )

            mock_extract.assert_called_once()
            call_kwargs = mock_extract.call_args.kwargs
            assert "config" in call_kwargs
            model_config = call_kwargs["config"]
            assert model_config.provider == "openai"
            assert model_config.model_id == "google/gemini-3.1-flash-lite"
            assert "base_url" in model_config.provider_kwargs
            assert model_config.provider_kwargs["base_url"] == "https://openrouter.ai/api/v1"
            assert "api_key" in model_config.provider_kwargs
            assert model_config.provider_kwargs["api_key"] == "test-key-123"
        finally:
            del os.environ["TEST_API_KEY"]

    @patch("services.adr_extract.lx.extract")
    def test_config_parameter_maps_model_url_to_base_url(self, mock_extract: MagicMock) -> None:
        """model_url is mapped to base_url in provider_kwargs for OpenAI provider."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        os.environ["TEST_API_KEY"] = "test-key"
        try:
            mock_extract.return_value = _make_langextract_result([])

            config = LangExtractConfig(
                model_url="https://custom-llm.example.com/v1",
                api_key_env="TEST_API_KEY",
                provider="openai",
            )
            extractor = ADRExtractor(config=config)
            extractor.extract_constraints(
                adr_text="Some text",
                adr_id="ADR-001",
                adr_path="docs/adr/ADR-001.md",
            )

            call_kwargs = mock_extract.call_args.kwargs
            model_config = call_kwargs["config"]
            assert model_config.provider_kwargs["base_url"] == "https://custom-llm.example.com/v1"
        finally:
            del os.environ["TEST_API_KEY"]

    @patch("services.adr_extract.lx.extract")
    def test_no_direct_model_id_or_api_key_in_call(self, mock_extract: MagicMock) -> None:
        """extract_constraints should use config= parameter, not model_id= or api_key= directly."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        os.environ["TEST_API_KEY"] = "test-key"
        try:
            mock_extract.return_value = _make_langextract_result([])

            config = LangExtractConfig(api_key_env="TEST_API_KEY")
            extractor = ADRExtractor(config=config)
            extractor.extract_constraints(
                adr_text="Some text",
                adr_id="ADR-001",
                adr_path="docs/adr/ADR-001.md",
            )

            call_kwargs = mock_extract.call_args.kwargs
            # config= should be present, model_id= and api_key= should NOT
            assert "config" in call_kwargs
            assert "model_id" not in call_kwargs
            assert "api_key" not in call_kwargs
            assert "model_url" not in call_kwargs
        finally:
            del os.environ["TEST_API_KEY"]


# ===========================================================================
# 3. ADRExtractor.extract_constraints: no constraints
# ===========================================================================


class TestExtractConstraintsNoResults:
    """Empty or no-constraint ADRs produce empty results, not errors."""

    @patch("services.adr_extract.lx.extract")
    def test_adr_with_no_constraints(self, mock_extract: MagicMock) -> None:
        """An ADR with no enforceable constraints returns empty constraints."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        mock_extract.return_value = _make_langextract_result([])

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        extractor = ADRExtractor(config=config)
        result = extractor.extract_constraints(
            adr_text=ADR_NO_CONSTRAINTS_TEXT,
            adr_id="ADR-006",
            adr_path="docs/adr/ADR-006-code-style.md",
        )

        assert len(result.constraints) == 0
        assert len(result.errors) == 0


# ===========================================================================
# 4. ADRExtractor.extract_constraints: malformed extrations
# ===========================================================================


class TestExtractConstraintsMalformed:
    """Malformed extractions are skipped and reported as errors."""

    @patch("services.adr_extract.lx.extract")
    def test_missing_char_interval_skipped(self, mock_extract: MagicMock) -> None:
        """Extractions without char_interval are skipped and logged."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        mock_extract.return_value = _make_langextract_result(
            [_make_extraction_no_char_interval()]
        )

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        extractor = ADRExtractor(config=config)
        result = extractor.extract_constraints(
            adr_text=ADR_001_TEXT,
            adr_id="ADR-001",
            adr_path="docs/adr/ADR-001-mysql-storage.md",
        )

        assert len(result.constraints) == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == "malformed_extraction"

    @patch("services.adr_extract.lx.extract")
    def test_invalid_predicate_skipped(self, mock_extract: MagicMock) -> None:
        """Extractions with invalid predicates are skipped and logged."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        invalid_extraction = _make_extraction(predicate="requires")
        mock_extract.return_value = _make_langextract_result([invalid_extraction])

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        extractor = ADRExtractor(config=config)
        result = extractor.extract_constraints(
            adr_text=ADR_001_TEXT,
            adr_id="ADR-001",
            adr_path="docs/adr/ADR-001-mysql-storage.md",
        )

        assert len(result.constraints) == 0
        assert len(result.errors) == 1
        assert "predicate" in result.errors[0].message.lower() or result.errors[0].error_type == "parse_failure"

    @patch("services.adr_extract.lx.extract")
    def test_mix_of_valid_and_malformed(self, mock_extract: MagicMock) -> None:
        """Valid constraints are kept; malformed ones are reported as errors."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        valid = _make_extraction(
            subject="services.*",
            predicate="prohibits_dependency",
            object_="db.mysql",
            justification="Direct MySQL connections prohibited.",
            char_start=45,
            char_end=120,
        )
        malformed = _make_extraction_no_char_interval()

        mock_extract.return_value = _make_langextract_result([valid, malformed])

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        extractor = ADRExtractor(config=config)
        result = extractor.extract_constraints(
            adr_text=ADR_001_TEXT,
            adr_id="ADR-001",
            adr_path="docs/adr/ADR-001-mysql-storage.md",
        )

        assert len(result.constraints) == 1
        assert len(result.errors) == 1


# ===========================================================================
# 5. ADRExtractor.extract_constraints: API failure
# ===========================================================================


class TestExtractConstraintsAPIFailure:
    """API failures are captured as ExtractionError, not raised."""

    @patch("services.adr_extract.lx.extract")
    def test_api_failure_returns_error(self, mock_extract: MagicMock) -> None:
        """Ollama API failure produces empty constraints with error details."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        mock_extract.side_effect = RuntimeError("Ollama API returned 429 rate limit")

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        extractor = ADRExtractor(config=config)
        result = extractor.extract_constraints(
            adr_text=ADR_001_TEXT,
            adr_id="ADR-001",
            adr_path="docs/adr/ADR-001-mysql-storage.md",
        )

        assert len(result.constraints) == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == "api_failure"
        assert "429" in result.errors[0].message or "rate limit" in result.errors[0].message

    @patch("services.adr_extract.lx.extract")
    def test_auth_failure_returns_error(self, mock_extract: MagicMock) -> None:
        """Authentication failure produces an api_failure error."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        mock_extract.side_effect = RuntimeError("Ollama API returned 401 unauthorized")

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        extractor = ADRExtractor(config=config)
        result = extractor.extract_constraints(
            adr_text=ADR_001_TEXT,
            adr_id="ADR-001",
            adr_path="docs/adr/ADR-001-mysql-storage.md",
        )

        assert len(result.errors) == 1
        assert result.errors[0].error_type == "api_failure"


# ===========================================================================
# 6. ADRExtractor.extract_from_file
# ===========================================================================


class TestExtractFromFile:
    """extract_from_file reads an ADR file and extracts constraints."""

    @patch("services.adr_extract.lx.extract")
    def test_extracts_from_markdown_file(self, mock_extract: MagicMock, tmp_path: Path) -> None:
        """extract_from_file reads .md file and passes text to extract_constraints."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        mock_extract.return_value = _make_langextract_result(
            [_make_extraction()]
        )

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        extractor = ADRExtractor(config=config)

        adr_path = tmp_path / "ADR-001-mysql-storage.md"
        adr_path.write_text(ADR_001_TEXT, encoding="utf-8")

        result = extractor.extract_from_file(adr_path)

        assert len(result.constraints) == 1
        assert mock_extract.called

    @patch("services.adr_extract.lx.extract")
    def test_adr_id_parsed_from_filename(self, mock_extract: MagicMock) -> None:
        """_parse_adr_id correctly extracts ADR IDs from various filenames."""
        from services.adr_extract import _parse_adr_id

        assert _parse_adr_id("docs/adr/ADR-001-mysql-storage.md") == "ADR-001"
        assert _parse_adr_id("ADR-003-auth-middleware.md") == "ADR-003"
        assert _parse_adr_id("docs/adr/ADR-999-legacy.md") == "ADR-999"
        assert _parse_adr_id("adr-005-microservices.md") == "ADR-005"

        # Non-ADR filename falls back to stem
        assert _parse_adr_id("docs/adr/style-guide.md") == "style-guide"


# ===========================================================================
# 7. ADRExtractor.extract_from_directory
# ===========================================================================


class TestExtractFromDirectory:
    """extract_from_directory scans an ADR directory and extracts from all .md files."""

    @patch.object(Path, "glob")
    @patch("services.adr_extract.lx.extract")
    def test_scans_all_adr_files(self, mock_extract: MagicMock, mock_glob: MagicMock) -> None:
        """extract_from_directory processes all ADR-*.md files in a directory."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        mock_extract.return_value = _make_langextract_result([_make_extraction()])

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        extractor = ADRExtractor(config=config)

        adr_dir = Path("/fake/adr/dir")
        mock_glob.return_value = [
            adr_dir / "ADR-001-mysql-storage.md",
            adr_dir / "ADR-003-auth-middleware.md",
        ]

        with patch.object(Path, "read_text", return_value=ADR_001_TEXT):
            results = extractor.extract_from_directory(adr_dir)

        assert len(results) == 2
        assert mock_extract.call_count == 2

    @patch("services.adr_extract.lx.extract")
    def test_empty_directory_returns_empty(self, mock_extract: MagicMock) -> None:
        """A directory with no ADR-*.md files returns empty results."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        mock_extract.return_value = _make_langextract_result([])

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        extractor = ADRExtractor(config=config)

        # Use a tmp_path with no ADR files
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            result = extractor.extract_from_directory(Path(tmpdir))
            assert result == []


# ===========================================================================
# 8. Config integration
# ===========================================================================


class TestConfigFromYaml:
    """LangExtractConfig can be loaded from repos.yaml."""

    def test_langextract_section_in_config(self) -> None:
        """GlobalConfig can load langextract section from repos.yaml."""
        from cli.config import GlobalConfig, LangExtractConfig, load_config

        # This tests that repos.yaml has a langextract section
        # and that load_config parses it into LangExtractConfig
        config = load_config()
        assert hasattr(config, "langextract")
        assert isinstance(config.langextract, LangExtractConfig)

    def test_config_passes_to_extractor(self) -> None:
        """ADRExtractor accepts a LangExtractConfig."""
        from services.adr_extract import ADRExtractor, LangExtractConfig

        config = LangExtractConfig(
            model_id="google/gemini-3.1-flash-lite",
            model_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
        )
        extractor = ADRExtractor(config=config)
        assert extractor.config.model_id == "google/gemini-3.1-flash-lite"
        assert extractor.config.model_url == "https://openrouter.ai/api/v1"