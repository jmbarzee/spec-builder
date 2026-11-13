"""Model adapter for spec builder activities: one `provider:model` setting per stage."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, TypeVar

from dotenv import find_dotenv, load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel
from pydantic_ai import Agent, BinaryContent
from pydantic_ai._json_schema import JsonSchemaTransformer
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.settings import ModelSettings

T = TypeVar("T", bound=BaseModel)

GEMINI_PROVIDER = "google"
DEFAULT_GENERATION_MODEL = f"{GEMINI_PROVIDER}:gemini-3.1-pro-preview"
DEFAULT_MODELS = {
    "acquire_content": DEFAULT_GENERATION_MODEL,
    "structured_extraction": DEFAULT_GENERATION_MODEL,
    "intent_classification": DEFAULT_GENERATION_MODEL,
    "extraction_critic": DEFAULT_GENERATION_MODEL,
    "draft_spec": DEFAULT_GENERATION_MODEL,
    "best_practices_review": DEFAULT_GENERATION_MODEL,
    "supervisor_decision": DEFAULT_GENERATION_MODEL,
    "design": DEFAULT_GENERATION_MODEL,
    "embedding": f"{GEMINI_PROVIDER}:gemini-embedding-001",
}
MAX_EMBED_BATCH_SIZE = 100
# Gemini rejects parts of full Pydantic JSON Schema, so its requests keep only the keys it accepts.
SUPPORTED_JSON_SCHEMA_KEYS = frozenset(
    {
        "$id",
        "$defs",
        "$ref",
        "$anchor",
        "type",
        "format",
        "title",
        "description",
        "enum",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "anyOf",
        "oneOf",
        "properties",
        "additionalProperties",
        "required",
        "propertyOrdering",
    }
)


class ModelExecutorError(RuntimeError):
    """Raised when model execution or its configuration fails."""


@dataclass(slots=True)
class Attachment:
    """Binary attachment supplied to a model request."""

    data: bytes
    mime_type: str


def _setting_name(stage_name: str) -> str:
    return f"SPEC_BUILDER_{stage_name.upper()}_MODEL"


def load_project_dotenv() -> None:
    """Load the nearest project `.env` without overriding exported variables."""
    # Tests and CLI entry points do not always run from the repo root, so the search starts at
    # the cwd and then at this module's directory.
    for usecwd in (True, False):
        if dotenv_path := find_dotenv(usecwd=usecwd):
            load_dotenv(dotenv_path=dotenv_path, override=False)
            return


def validate_settings() -> None:
    """Fail on a retired `SPEC_BUILDER_GEMINI_*` variable, naming its replacement."""
    load_project_dotenv()
    for name in sorted(os.environ):
        if name.startswith("SPEC_BUILDER_GEMINI_"):
            suffix = name.removeprefix("SPEC_BUILDER_GEMINI_")
            replacement = (
                f"SPEC_BUILDER_{suffix}"
                if suffix.endswith("_MODEL")
                else f"SPEC_BUILDER_MODEL_{suffix}"
            )
            raise ModelExecutorError(f"{name} is retired; set {replacement} instead.")
    for stage_name in DEFAULT_MODELS:
        resolve_model(stage_name)


def resolve_model(stage_name: str) -> str:
    """Resolve a stage's `provider:model` string from its setting."""
    setting = _setting_name(stage_name)
    spec = os.getenv(setting, DEFAULT_MODELS[stage_name])
    provider, _, model = spec.partition(":")
    if not provider or not model:
        raise ModelExecutorError(f"{setting}={spec!r} must be 'provider:model'.")
    return spec


class _GeminiSchemaTransformer(JsonSchemaTransformer):
    """Project each schema node down to the subset Gemini accepts."""

    def transform(self, schema: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in schema:
            # Pydantic may put sibling keys beside `$ref`, which Gemini rejects.
            return {key: value for key, value in schema.items() if key.startswith("$")}
        kept = {key: value for key, value in schema.items() if key in SUPPORTED_JSON_SCHEMA_KEYS}
        if kept.get("properties") and "propertyOrdering" not in kept:
            # Declared field order keeps Gemini's output predictable and diffable.
            kept["propertyOrdering"] = list(kept["properties"])
        return kept


def _gemini_model(name: str, provider: GoogleProvider | None = None) -> GoogleModel:
    return GoogleModel(
        name,
        # `GEMINI_API_KEY` wins over `GOOGLE_API_KEY`, as it did before Pydantic AI.
        provider=provider or GoogleProvider(api_key=os.getenv("GEMINI_API_KEY")),
        profile=ModelProfile(
            json_schema_transformer=_GeminiSchemaTransformer,
            # JSON response mode with `responseJsonSchema`, as these stages always sent.
            default_structured_output_mode="native",
        ),
    )


def _build_model(spec: str) -> Model:
    provider, _, name = spec.partition(":")
    return _gemini_model(name) if provider == GEMINI_PROVIDER else infer_model(spec)


def _summarize_error(error: Exception | None, *, max_length: int = 1_200) -> str:
    """Return a compact error summary suitable for Temporal failure payloads."""
    if error is None:
        return "unknown error"
    message = " ".join(str(error).split()) or error.__class__.__name__
    if len(message) <= max_length:
        return message
    return f"{message[: max_length - 1]}…"


class ModelExecutor:
    """Structured generation through Pydantic AI, and embeddings through `google-genai`."""

    def __init__(
        self,
        *,
        client: genai.Client | None = None,
        request_timeout_seconds: int | None = None,
        max_attempts: int | None = None,
    ) -> None:
        validate_settings()
        self._client = client
        self._request_timeout_seconds = request_timeout_seconds or int(
            os.getenv("SPEC_BUILDER_MODEL_REQUEST_TIMEOUT_SECONDS", "60")
        )
        self._max_attempts = max_attempts or int(os.getenv("SPEC_BUILDER_MODEL_MAX_ATTEMPTS", "3"))

    def _embedding_client(self) -> genai.Client:
        if self._client is None:
            # Retries are the executor's: one attempt per SDK call keeps failures Temporal-readable.
            self._client = genai.Client(
                api_key=os.getenv("GEMINI_API_KEY"),
                http_options=types.HttpOptions(
                    timeout=self._request_timeout_seconds * 1000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._client

    async def generate_structured(
        self,
        *,
        stage_name: str,
        prompt: str,
        response_model: type[T],
        system_instruction: str,
        attachments: list[Attachment] | None = None,
        temperature: float = 0.1,
        max_output_tokens: int = 8_192,
    ) -> T:
        """Run a structured generation request and parse it into `response_model`."""
        agent = Agent(
            _build_model(resolve_model(stage_name)),
            output_type=response_model,
            # No output retries: the loop below owns retries, so each attempt is one request.
            retries=0,
            instructions=system_instruction,
            model_settings=ModelSettings(
                temperature=temperature,
                max_tokens=max_output_tokens,
                timeout=self._request_timeout_seconds,
            ),
        )
        # The prompt is always first; binary inputs follow so callers keep prompt construction
        # separate from attachment loading.
        content = [
            prompt,
            *(BinaryContent(a.data, media_type=a.mime_type) for a in attachments or []),
        ]
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return (await agent.run(content)).output
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self._max_attempts:
                    # Bounded backoff smooths transient API failures without stretching the
                    # activity too far.
                    await asyncio.sleep(min(attempt, 3))
        raise ModelExecutorError(
            f"Structured generation failed for stage '{stage_name}': {_summarize_error(last_error)}"
        ) from None

    def embed_texts(
        self,
        texts: list[str],
        *,
        task_type: str,
        title: str | None = None,
    ) -> list[list[float]]:
        """Return embeddings for the supplied texts."""
        if not texts:
            return []
        spec = resolve_model("embedding")
        provider, _, model = spec.partition(":")
        if provider != GEMINI_PROVIDER:
            raise ModelExecutorError(
                f"{_setting_name('embedding')}={spec!r}: embeddings support only {GEMINI_PROVIDER}."
            )
        config = types.EmbedContentConfig(taskType=task_type, title=title)
        vectors: list[list[float]] = []
        try:
            # Embedding APIs accept bounded batches, so batching lives here, not in every caller.
            for start in range(0, len(texts), MAX_EMBED_BATCH_SIZE):
                response = self._embedding_client().models.embed_content(
                    model=model,
                    contents=texts[start : start + MAX_EMBED_BATCH_SIZE],
                    config=config,
                )
                vectors.extend(embedding.values for embedding in response.embeddings)
        except Exception as exc:  # noqa: BLE001
            raise ModelExecutorError(f"Embedding failed: {_summarize_error(exc)}") from None
        return vectors
