"""Tests for the model executor."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from pydantic_ai.messages import BinaryContent, ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from src.workflows.spec_builder import model_executor
from src.workflows.spec_builder.model_executor import (
    DEFAULT_MODELS,
    Attachment,
    ModelExecutor,
    ModelExecutorError,
    _gemini_model,
    resolve_model,
)


class ExampleResponse(BaseModel):
    """Simple response schema for executor tests."""

    message: str


class SchemaCarrierResponse(BaseModel):
    """Response model that exercises defaults, optional fields and open-ended dict items."""

    contracts: list[dict[str, Any]] = Field(default_factory=list)
    optional_note: str | None = None


def _answer(info: AgentInfo, **payload: object) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])


def _use_model(monkeypatch: pytest.MonkeyPatch, model: Model) -> None:
    monkeypatch.setattr(model_executor, "_build_model", lambda _spec: model)


def _walk_keys(payload: object) -> list[str]:
    """Return every key found in a nested JSON-like payload."""
    if isinstance(payload, dict):
        return [*payload, *(key for value in payload.values() for key in _walk_keys(value))]
    if isinstance(payload, list):
        return [key for item in payload for key in _walk_keys(item)]
    return []


class FakeEmbedModels:
    """Fake `client.models` that records batch sizes and returns indexed vectors."""

    def __init__(self) -> None:
        self.models: list[str] = []
        self.batch_sizes: list[int] = []

    def embed_content(self, *, model: str, contents: list[str], **_: object) -> object:
        """Return one vector per text, valued by the text's index."""
        self.models.append(model)
        self.batch_sizes.append(len(contents))
        return SimpleNamespace(
            embeddings=[
                SimpleNamespace(values=[float(text.rsplit("-", 1)[1])]) for text in contents
            ]
        )


class FakeEmbedClient:
    """Fake Gemini client exposing `FakeEmbedModels`."""

    def __init__(self) -> None:
        self.models = FakeEmbedModels()


class TestResolveModel:
    """Behavior tests for per-stage model settings."""

    @pytest.mark.parametrize("stage_name", list(DEFAULT_MODELS))
    def test_resolve_model_should_default_to_gemini_when_unset(self, stage_name: str) -> None:
        """
        Scenario: No settings
        When a stage resolves its model
        Then it should get the Gemini model it used before settings existed
        """
        expected = (
            "google:gemini-embedding-001"
            if stage_name == "embedding"
            else "google:gemini-3.1-pro-preview"
        )

        assert resolve_model(stage_name) == expected

    def test_resolve_model_should_honor_the_stage_setting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Scenario: A stage setting
        Given SPEC_BUILDER_DRAFT_SPEC_MODEL names another provider
        When draft_spec resolves its model
        Then only that stage should change
        """
        monkeypatch.setenv("SPEC_BUILDER_DRAFT_SPEC_MODEL", "anthropic:claude-sonnet-4-5")

        assert resolve_model("draft_spec") == "anthropic:claude-sonnet-4-5"
        assert resolve_model("extraction_critic") == DEFAULT_MODELS["extraction_critic"]

    def test_resolve_model_should_name_the_setting_when_the_provider_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Scenario: A bare model id
        Given a setting without `provider:`
        When the stage resolves its model
        Then the error should name the setting
        """
        monkeypatch.setenv("SPEC_BUILDER_DRAFT_SPEC_MODEL", "gemini-3.1-pro-preview")

        with pytest.raises(ModelExecutorError, match="SPEC_BUILDER_DRAFT_SPEC_MODEL"):
            resolve_model("draft_spec")

    @pytest.mark.parametrize(
        ("retired", "replacement"),
        [
            ("SPEC_BUILDER_GEMINI_DRAFT_SPEC_MODEL", "SPEC_BUILDER_DRAFT_SPEC_MODEL"),
            ("SPEC_BUILDER_GEMINI_EMBEDDING_MODEL", "SPEC_BUILDER_EMBEDDING_MODEL"),
            ("SPEC_BUILDER_GEMINI_MAX_ATTEMPTS", "SPEC_BUILDER_MODEL_MAX_ATTEMPTS"),
            (
                "SPEC_BUILDER_GEMINI_REQUEST_TIMEOUT_SECONDS",
                "SPEC_BUILDER_MODEL_REQUEST_TIMEOUT_SECONDS",
            ),
        ],
    )
    def test_init_should_fail_naming_the_replacement_when_a_retired_setting_is_set(
        self, monkeypatch: pytest.MonkeyPatch, retired: str, replacement: str
    ) -> None:
        """
        Scenario: A deployment still sets a retired variable
        When the executor starts
        Then it should fail naming the variable that replaces it
        """
        monkeypatch.setenv(retired, "x")

        with pytest.raises(ModelExecutorError, match=f"{retired}.*{replacement}"):
            ModelExecutor()


class TestDotenv:
    """Behavior tests for `.env` loading."""

    def test_init_should_load_settings_from_a_dotenv_in_a_parent_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """
        Scenario: Starting the worker from a nested subdirectory
        Given a `.env` in a parent of the working directory
        When the executor initializes
        Then it should load that file's settings
        """
        (tmp_path / ".env").write_text("SPEC_BUILDER_MODEL_MAX_ATTEMPTS=5\n", encoding="utf-8")
        nested = tmp_path / "nested" / "deeper"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        monkeypatch.delenv("SPEC_BUILDER_MODEL_MAX_ATTEMPTS", raising=False)

        assert ModelExecutor()._max_attempts == 5

    def test_init_should_not_override_exported_variables_with_dotenv(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """
        Scenario: Shell and `.env` both set a variable
        When the executor initializes
        Then the exported value should win
        """
        (tmp_path / ".env").write_text("SPEC_BUILDER_MODEL_MAX_ATTEMPTS=5\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SPEC_BUILDER_MODEL_MAX_ATTEMPTS", "7")

        assert ModelExecutor()._max_attempts == 7


class TestGenerateStructured:
    """Behavior tests for structured generation."""

    @pytest.mark.asyncio
    async def test_generate_structured_should_send_a_gemini_compatible_schema_over_http(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Scenario: Gemini requests through Pydantic AI
        Given a response model with defaults, optional fields and open-ended dict items
        When the default Gemini model generates against a stubbed HTTP endpoint
        Then the request should be JSON response mode carrying only keys Gemini accepts,
        and the reply should parse
        """
        requests: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            text = '{"contracts": [{"name": "SpecBuilderInput"}]}'
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": [{"text": text}]},
                            "finishReason": "STOP",
                        }
                    ]
                },
            )

        client = genai.Client(
            api_key="test-key",
            http_options=types.HttpOptions(
                httpx_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
            ),
        )
        _use_model(
            monkeypatch,
            _gemini_model("gemini-3.1-pro-preview", provider=GoogleProvider(client=client)),
        )

        result = await ModelExecutor().generate_structured(
            stage_name="draft_spec",
            prompt="hello",
            response_model=SchemaCarrierResponse,
            system_instruction="system",
        )

        assert result.contracts == [{"name": "SpecBuilderInput"}]
        assert "tools" not in requests[0]
        config = requests[0]["generationConfig"]
        assert config["responseMimeType"] == "application/json"
        schema = config["responseJsonSchema"]
        assert schema["properties"]["contracts"]["items"]["additionalProperties"]
        assert "default" not in _walk_keys(schema)
        assert schema["propertyOrdering"] == ["contracts", "optional_note"]

    @pytest.mark.asyncio
    async def test_generate_structured_should_retry_when_the_first_attempt_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Scenario: Recovering from a transient failure
        Given the first model call fails and the second succeeds
        When structured generation runs
        Then it should retry and return the parsed model
        """
        calls = 0

        def respond(_: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient failure")
            return _answer(info, message="ok")

        _use_model(monkeypatch, FunctionModel(respond))
        monkeypatch.setattr(model_executor.asyncio, "sleep", _no_sleep)

        result = await ModelExecutor(max_attempts=2).generate_structured(
            stage_name="draft_spec",
            prompt="hello",
            response_model=ExampleResponse,
            system_instruction="s",
        )

        assert result.message == "ok"
        assert calls == 2

    @pytest.mark.asyncio
    async def test_generate_structured_should_send_the_prompt_then_attachments(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Scenario: A multimodal request
        When structured generation runs with an attachment
        Then the model should see the prompt first and the attachment as binary content
        """
        seen: list[object] = []

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.extend(messages[0].parts[-1].content)
            return _answer(info, message="ok")

        _use_model(monkeypatch, FunctionModel(respond))

        await ModelExecutor().generate_structured(
            stage_name="acquire_content",
            prompt="hello",
            response_model=ExampleResponse,
            system_instruction="s",
            attachments=[Attachment(data=b"abc", mime_type="image/png")],
        )

        assert seen[0] == "hello"
        assert seen[1] == BinaryContent(b"abc", media_type="image/png")

    @pytest.mark.asyncio
    async def test_generate_structured_should_shorten_final_error_messages_for_temporal_failures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Scenario: A provider returns an oversized failure payload
        When every attempt fails
        Then the error should be compact and not chain the huge cause
        """

        def respond(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
            raise RuntimeError("provider-error " + "x" * 3000)

        _use_model(monkeypatch, FunctionModel(respond))

        with pytest.raises(ModelExecutorError) as exc_info:
            await ModelExecutor(max_attempts=1).generate_structured(
                stage_name="structured_extraction",
                prompt="hello",
                response_model=ExampleResponse,
                system_instruction="s",
            )

        assert "structured_extraction" in str(exc_info.value)
        assert len(str(exc_info.value)) < 1_400
        assert exc_info.value.__cause__ is None

    @pytest.mark.parametrize("stage_name", [name for name in DEFAULT_MODELS if name != "embedding"])
    def test_build_model_should_build_every_default_as_a_gemini_model_with_the_transformer(
        self, monkeypatch: pytest.MonkeyPatch, stage_name: str
    ) -> None:
        """
        Scenario: Defaults go through the real model builders
        Given a dummy key and no network
        When a stage's default spec resolves through `_build_model` and `infer_model`
        Then both should yield a GoogleModel, and the first should carry the Gemini transformer
        """
        monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
        spec = resolve_model(stage_name)

        built = model_executor._build_model(spec)
        inferred = infer_model(spec)

        assert isinstance(built, GoogleModel)
        assert isinstance(inferred, GoogleModel)
        assert built.profile["json_schema_transformer"] is model_executor._GeminiSchemaTransformer

    def test_build_model_should_resolve_a_second_provider_without_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Scenario: A non-Gemini provider spec
        Given a dummy key
        When the spec resolves through Pydantic AI
        Then it should yield that provider's model with the requested name
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")

        model = model_executor._build_model("anthropic:claude-sonnet-4-5")

        assert isinstance(model, AnthropicModel)
        assert model.model_name == "claude-sonnet-4-5"


class TestEmbedTexts:
    """Behavior tests for embeddings."""

    def test_embed_texts_should_batch_requests_and_preserve_order(self) -> None:
        """
        Scenario: More texts than one embedding request allows
        When the executor embeds 205 texts
        Then it should split them into 100/100/5 and keep order, using the configured model id
        """
        client = FakeEmbedClient()
        texts = [f"text-{index}" for index in range(205)]

        vectors = ModelExecutor(client=client).embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")

        assert client.models.batch_sizes == [100, 100, 5]
        assert set(client.models.models) == {"gemini-embedding-001"}
        assert vectors == [[float(index)] for index in range(205)]

    def test_embed_texts_should_reject_a_non_gemini_provider_naming_the_setting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Scenario: Embedding on another provider
        When SPEC_BUILDER_EMBEDDING_MODEL names one
        Then embedding should fail naming the setting
        """
        monkeypatch.setenv("SPEC_BUILDER_EMBEDDING_MODEL", "openai:text-embedding-3-small")

        with pytest.raises(ModelExecutorError, match="SPEC_BUILDER_EMBEDDING_MODEL"):
            ModelExecutor(client=FakeEmbedClient()).embed_texts(["a"], task_type="RETRIEVAL_QUERY")

    def test_embed_texts_should_shorten_final_error_messages_for_temporal_failures(self) -> None:
        """
        Scenario: The embedding call returns an oversized failure payload
        When embeddings are requested
        Then the error should be compact and not chain the huge cause
        """

        class Failing:
            def embed_content(self, **_: object) -> object:
                raise RuntimeError("embed-provider-error " + "y" * 3000)

        client = SimpleNamespace(models=Failing())

        with pytest.raises(ModelExecutorError) as exc_info:
            ModelExecutor(client=client).embed_texts(["alpha"], task_type="RETRIEVAL_DOCUMENT")

        assert "Embedding failed" in str(exc_info.value)
        assert len(str(exc_info.value)) < 1_400
        assert exc_info.value.__cause__ is None


async def _no_sleep(_: float) -> None:
    return None


def test_readme_should_document_every_stage_setting_with_its_default() -> None:
    """
    Scenario: Settings documentation
    Given the README's Model Configuration section
    Then every stage name and each default model should appear in it
    """
    readme = (Path(__file__).parents[4] / "README.md").read_text()

    for stage_name, spec in DEFAULT_MODELS.items():
        assert stage_name.upper() in readme
        assert spec in readme
