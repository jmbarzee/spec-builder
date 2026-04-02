"""Tests for the Gemini executor."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, Field

from src.workflows.spec_builder.gemini_executor import GeminiExecutor, GeminiExecutorError


class ExampleResponse(BaseModel):
    """Simple response schema for executor tests."""

    message: str


class FakeModels:
    """Fake `client.models` implementation."""

    def __init__(self) -> None:
        self.generate_calls = 0
        self.embed_calls = 0

    def generate_content(self, **_: object) -> object:
        """Return a parsed response on the second call."""
        self.generate_calls += 1
        if self.generate_calls == 1:
            raise RuntimeError("transient failure")
        return SimpleNamespace(parsed=ExampleResponse(message="ok"), text=None)

    def embed_content(self, **_: object) -> object:
        """Return deterministic embeddings."""
        self.embed_calls += 1
        return SimpleNamespace(
            embeddings=[
                SimpleNamespace(values=[1.0, 0.0]),
                SimpleNamespace(values=[0.0, 1.0]),
            ]
        )


class FakeClient:
    """Fake Gemini client wrapper."""

    def __init__(self) -> None:
        self.models = FakeModels()


class TextOnlyModels:
    """Fake model client that returns JSON text instead of `parsed`."""

    def __init__(self) -> None:
        self.last_contents: object | None = None
        self.last_config: object | None = None

    def generate_content(self, **kwargs: object) -> object:
        """Return valid JSON text."""
        self.last_contents = kwargs["contents"]
        self.last_config = kwargs["config"]
        return SimpleNamespace(parsed=None, text='{"message":"from-text"}')

    def embed_content(self, **_: object) -> object:
        """Return a single embedding."""
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.5, 0.5])])


class TextOnlyClient:
    """Fake Gemini client wrapper that exposes `TextOnlyModels`."""

    def __init__(self) -> None:
        self.models = TextOnlyModels()


class SchemaCarrierResponse(BaseModel):
    """Response model that exercises optional fields and open-ended dict items."""

    contracts: list[dict[str, Any]] = Field(default_factory=list)
    optional_note: str | None = None


class AlwaysFailModels:
    """Fake model client that always raises a large error."""

    def generate_content(self, **_: object) -> object:
        """Raise a long error message to simulate provider payload bloat."""
        raise RuntimeError("provider-error " + ("x" * 3000))

    def embed_content(self, **_: object) -> object:
        """Unused in this test."""
        return SimpleNamespace(embeddings=[])


class AlwaysFailClient:
    """Fake Gemini client wrapper that always fails generation."""

    def __init__(self) -> None:
        self.models = AlwaysFailModels()


class AlwaysFailEmbedModels:
    """Fake model client that always raises a large embedding error."""

    def generate_content(self, **_: object) -> object:
        """Unused in this test."""
        return SimpleNamespace(parsed=ExampleResponse(message="ok"), text=None)

    def embed_content(self, **_: object) -> object:
        """Raise a long error message to simulate provider payload bloat."""
        raise RuntimeError("embed-provider-error " + ("y" * 3000))


class AlwaysFailEmbedClient:
    """Fake Gemini client wrapper that always fails embeddings."""

    def __init__(self) -> None:
        self.models = AlwaysFailEmbedModels()


class SchemaCarrierModels:
    """Fake model client that captures config and returns schema-carrier JSON text."""

    def __init__(self) -> None:
        self.last_config: object | None = None

    def generate_content(self, **kwargs: object) -> object:
        """Return JSON matching `SchemaCarrierResponse`."""
        self.last_config = kwargs["config"]
        return SimpleNamespace(
            parsed=None,
            text='{"contracts":[{"name":"SpecBuilderInput"}],"optional_note":null}',
        )

    def embed_content(self, **_: object) -> object:
        """Unused in this test."""
        return SimpleNamespace(embeddings=[])


class SchemaCarrierClient:
    """Fake Gemini client wrapper that exposes `SchemaCarrierModels`."""

    def __init__(self) -> None:
        self.models = SchemaCarrierModels()


class BatchedEmbedModels:
    """Fake embed client that records chunk sizes and returns indexed vectors."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def generate_content(self, **_: object) -> object:
        """Unused in this test."""
        return SimpleNamespace(parsed=ExampleResponse(message="ok"), text=None)

    def embed_content(self, **kwargs: object) -> object:
        """Return vectors that preserve the input ordering across batches."""
        contents = kwargs["contents"]
        assert isinstance(contents, list)
        self.batch_sizes.append(len(contents))
        return SimpleNamespace(
            embeddings=[
                SimpleNamespace(values=[float(int(text.rsplit("-", 1)[1]))]) for text in contents
            ]
        )


class BatchedEmbedClient:
    """Fake Gemini client wrapper that exposes `BatchedEmbedModels`."""

    def __init__(self) -> None:
        self.models = BatchedEmbedModels()


def _walk_keys(payload: object) -> list[str]:
    """Return every key found in a nested JSON-like payload."""
    if isinstance(payload, dict):
        keys = list(payload.keys())
        for value in payload.values():
            keys.extend(_walk_keys(value))
        return keys
    if isinstance(payload, list):
        keys: list[str] = []
        for item in payload:
            keys.extend(_walk_keys(item))
        return keys
    return []


class TestGeminiExecutor:
    """Behavior tests for the Gemini executor."""

    def test_generate_structured_should_require_api_key_or_client_when_unconfigured(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Scenario: Missing Gemini configuration
        Given no API key or injected client is available
        When structured generation is attempted
        Then the executor should fail with a clear configuration error
        """
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        executor = GeminiExecutor(api_key=None, client=None)

        with pytest.raises(GeminiExecutorError):
            executor.generate_structured(
                stage_name="draft_spec",
                prompt="hello",
                response_model=ExampleResponse,
                system_instruction="system",
            )

    def test_init_should_load_dotenv_from_current_directory_when_shell_env_is_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """
        Scenario: Loading Gemini configuration from a local `.env`
        Given the current working directory contains a `.env` file
        When the executor is initialized without an injected API key
        Then it should load Gemini settings from that `.env`
        """
        env_path = tmp_path / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "GEMINI_API_KEY=from-dotenv",
                    "SPEC_BUILDER_GEMINI_MAX_ATTEMPTS=5",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("SPEC_BUILDER_GEMINI_MAX_ATTEMPTS", raising=False)

        executor = GeminiExecutor(client=TextOnlyClient())

        assert executor._dotenv_path == env_path
        assert executor._api_key == "from-dotenv"
        assert executor._max_attempts == 5

    def test_init_should_not_override_existing_shell_environment_values_with_dotenv(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """
        Scenario: Respecting an exported shell Gemini API key
        Given both the shell environment and a local `.env` define the key
        When the executor initializes
        Then the exported shell value should win
        """
        env_path = tmp_path / ".env"
        env_path.write_text("GEMINI_API_KEY=from-dotenv\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GEMINI_API_KEY", "from-shell")

        executor = GeminiExecutor(client=TextOnlyClient())

        assert executor._dotenv_path == env_path
        assert os.environ["GEMINI_API_KEY"] == "from-shell"
        assert executor._api_key == "from-shell"

    def test_init_should_load_dotenv_from_a_parent_directory_of_the_current_working_directory(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """
        Scenario: Starting the worker from a nested subdirectory
        Given a `.env` exists in a parent directory of the current working directory
        When the executor initializes
        Then it should still find and load that parent `.env`
        """
        env_path = tmp_path / ".env"
        env_path.write_text("GEMINI_API_KEY=from-parent-dotenv\n", encoding="utf-8")
        nested = tmp_path / "nested" / "deeper"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

        executor = GeminiExecutor(client=TextOnlyClient())

        assert executor._dotenv_path == env_path
        assert executor._api_key == "from-parent-dotenv"

    def test_generate_structured_should_retry_and_parse_response_when_first_attempt_fails(
        self,
    ) -> None:
        """
        Scenario: Recovering from a transient Gemini failure
        Given the first generation call fails and the second succeeds
        When structured generation is invoked
        Then the executor should retry and return the parsed model
        """
        fake_client = FakeClient()
        executor = GeminiExecutor(client=fake_client, max_attempts=2)

        result = executor.generate_structured(
            stage_name="draft_spec",
            prompt="hello",
            response_model=ExampleResponse,
            system_instruction="system",
        )

        assert result.message == "ok"
        assert fake_client.models.generate_calls == 2

    def test_embed_texts_should_return_embedding_vectors_when_client_responds(
        self,
    ) -> None:
        """
        Scenario: Requesting embeddings for retrieval
        Given the Gemini embedding endpoint returns vectors
        When the executor embeds multiple texts
        Then the resulting vectors should be returned in order
        """
        fake_client = FakeClient()
        executor = GeminiExecutor(client=fake_client)

        vectors = executor.embed_texts(
            ["alpha", "beta"],
            task_type="RETRIEVAL_DOCUMENT",
        )

        assert vectors == [[1.0, 0.0], [0.0, 1.0]]
        assert fake_client.models.embed_calls == 1

    def test_embed_texts_should_batch_requests_to_respect_gemini_embed_limit(
        self,
    ) -> None:
        """
        Scenario: Embedding more texts than Gemini allows in a single batch
        Given more than 100 texts need embeddings
        When the executor requests document embeddings
        Then it should split them into API-compliant batches and preserve ordering
        """
        fake_client = BatchedEmbedClient()
        executor = GeminiExecutor(client=fake_client)
        texts = [f"text-{index}" for index in range(205)]

        vectors = executor.embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")

        assert fake_client.models.batch_sizes == [100, 100, 5]
        assert vectors == [[float(index)] for index in range(205)]

    def test_generate_structured_should_parse_json_text_and_include_attachments_when_needed(
        self,
    ) -> None:
        """
        Scenario: Parsing JSON text responses
        Given Gemini returns JSON text instead of a parsed payload
        When structured generation is invoked with attachments
        Then the executor should parse the JSON and include the attachment parts
        """
        from src.workflows.spec_builder.gemini_executor import GeminiAttachment

        fake_client = TextOnlyClient()
        executor = GeminiExecutor(client=fake_client)

        result = executor.generate_structured(
            stage_name="draft_spec",
            prompt="hello",
            response_model=ExampleResponse,
            system_instruction="system",
            attachments=[GeminiAttachment(data=b"abc", mime_type="text/plain", filename="note.txt")],
        )

        assert result.message == "from-text"
        assert len(fake_client.models.last_contents) == 2

    def test_model_resolution_should_honor_environment_overrides(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Scenario: Stage-specific model configuration
        Given environment overrides exist for generation and embedding models
        When the executor resolves model names
        Then the override values should win over the defaults
        """
        monkeypatch.setenv("SPEC_BUILDER_GEMINI_DRAFT_SPEC_MODEL", "custom-draft-model")
        monkeypatch.setenv("SPEC_BUILDER_GEMINI_EMBEDDING_MODEL", "custom-embedding-model")
        executor = GeminiExecutor(client=TextOnlyClient())

        assert executor.resolve_generation_model("draft_spec") == "custom-draft-model"
        assert executor.resolve_embedding_model() == "custom-embedding-model"

    def test_generate_structured_should_use_response_json_schema_subset_for_pydantic_models(
        self,
    ) -> None:
        """
        Scenario: Gemini structured output with nested Pydantic schemas
        Given a response model whose JSON schema includes defaults and open-ended dict items
        When structured generation is invoked
        Then the executor should send a sanitized `response_json_schema` instead of `response_schema`
        """
        fake_client = SchemaCarrierClient()
        executor = GeminiExecutor(client=fake_client)

        result = executor.generate_structured(
            stage_name="draft_spec",
            prompt="hello",
            response_model=SchemaCarrierResponse,
            system_instruction="system",
        )

        config = fake_client.models.last_config
        assert result.contracts == [{"name": "SpecBuilderInput"}]
        assert config is not None
        assert config.response_schema is None
        assert config.response_json_schema["properties"]["contracts"]["items"]["additionalProperties"]
        assert "default" not in _walk_keys(config.response_json_schema)

    def test_generate_structured_should_shorten_final_error_messages_for_temporal_failures(
        self,
    ) -> None:
        """
        Scenario: Gemini provider returns an oversized failure payload
        Given every generation attempt raises a very large provider error
        When structured generation exhausts retries
        Then the executor should raise a compact error message without chaining the huge cause
        """
        executor = GeminiExecutor(client=AlwaysFailClient(), max_attempts=1)

        with pytest.raises(GeminiExecutorError) as exc_info:
            executor.generate_structured(
                stage_name="structured_extraction",
                prompt="hello",
                response_model=ExampleResponse,
                system_instruction="system",
            )

        assert "structured_extraction" in str(exc_info.value)
        assert len(str(exc_info.value)) < 1_400
        assert exc_info.value.__cause__ is None

    def test_embed_texts_should_shorten_final_error_messages_for_temporal_failures(
        self,
    ) -> None:
        """
        Scenario: Gemini embedding returns an oversized failure payload
        Given the embedding call raises a very large provider error
        When embeddings are requested
        Then the executor should raise a compact error message without chaining the huge cause
        """
        executor = GeminiExecutor(client=AlwaysFailEmbedClient(), max_attempts=1)

        with pytest.raises(GeminiExecutorError) as exc_info:
            executor.embed_texts(
                ["alpha"],
                task_type="RETRIEVAL_DOCUMENT",
            )

        assert "Gemini embedding failed" in str(exc_info.value)
        assert len(str(exc_info.value)) < 1_400
        assert exc_info.value.__cause__ is None
