"""Gemini SDK adapter for spec builder activities."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from google import genai
from google.genai import types
from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)
# Gemini rejects parts of full Pydantic JSON Schema, so structured generation must project schemas
# down to the subset the API currently accepts.
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


class GeminiExecutorError(RuntimeError):
    """Raised when Gemini execution fails."""


@dataclass(slots=True)
class GeminiAttachment:
    """Binary attachment supplied to a Gemini request."""

    data: bytes
    mime_type: str
    filename: str


class GeminiExecutor:
    """Small wrapper around `google-genai` with structured parsing and retries."""

    DEFAULT_GENERATION_MODEL = "gemini-3.1-pro-preview"
    DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
    MAX_EMBED_BATCH_SIZE = 100

    @staticmethod
    def _candidate_dotenv_paths() -> list[Path]:
        """Return likely `.env` paths from the cwd and this module's ancestry."""
        # Tests and CLI entry points do not always run from the repo root, so we probe both the
        # current working tree and this module's parent chain for a project-local `.env`.
        candidates: list[Path] = []
        seen: set[Path] = set()
        roots = [Path.cwd().resolve(), Path(__file__).resolve().parent]
        for root in roots:
            for directory in (root, *root.parents):
                candidate = directory / ".env"
                if candidate not in seen:
                    seen.add(candidate)
                    candidates.append(candidate)
        return candidates

    @staticmethod
    def _load_project_dotenv() -> Path | None:
        """Load the nearest project `.env` without overriding exported variables."""
        dotenv_path = find_dotenv(usecwd=True)
        if dotenv_path:
            load_dotenv(dotenv_path=dotenv_path, override=False)
            return Path(dotenv_path)
        for candidate in GeminiExecutor._candidate_dotenv_paths():
            if candidate.exists() and candidate.is_file():
                load_dotenv(dotenv_path=candidate, override=False)
                return candidate
        return None

    def __init__(
        self,
        *,
        client: genai.Client | None = None,
        api_key: str | None = None,
        request_timeout_seconds: int | None = None,
        max_attempts: int | None = None,
    ) -> None:
        # The executor allows direct client injection for tests, but still loads env-derived
        # defaults so production activities can construct it with zero explicit configuration.
        self._dotenv_path = self._load_project_dotenv()
        self._client = client
        self._api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self._request_timeout_seconds = request_timeout_seconds or int(
            os.getenv("SPEC_BUILDER_GEMINI_REQUEST_TIMEOUT_SECONDS", "60")
        )
        self._max_attempts = max_attempts or int(os.getenv("SPEC_BUILDER_GEMINI_MAX_ATTEMPTS", "3"))

    def _http_options(self) -> types.HttpOptions:
        # Gemini SDK retries are disabled here because the executor already applies a small,
        # stage-level retry loop with clearer Temporal-friendly error reporting.
        return types.HttpOptions(
            timeout=self._request_timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        )

    def _client_or_raise(self) -> genai.Client:
        if self._client is not None:
            return self._client
        if not self._api_key:
            # Surface dotenv discovery details in the error so local setup failures are diagnosable
            # from Temporal activity logs without having to inspect process environment manually.
            dotenv_detail = (
                f" Loaded .env from {self._dotenv_path}."
                if self._dotenv_path is not None
                else f" No .env file was found starting from {Path.cwd()}."
            )
            raise GeminiExecutorError(
                "Gemini API key is not configured. Set GEMINI_API_KEY or GOOGLE_API_KEY."
                + dotenv_detail
            )
        self._client = genai.Client(api_key=self._api_key, http_options=self._http_options())
        return self._client

    @staticmethod
    def _model_env_var(stage_name: str) -> str:
        normalized = stage_name.upper().replace("-", "_")
        return f"SPEC_BUILDER_GEMINI_{normalized}_MODEL"

    def resolve_generation_model(self, stage_name: str) -> str:
        """Resolve the configured generation model for a stage."""
        return os.getenv(self._model_env_var(stage_name), self.DEFAULT_GENERATION_MODEL)

    def resolve_embedding_model(self) -> str:
        """Resolve the configured embedding model."""
        return os.getenv("SPEC_BUILDER_GEMINI_EMBEDDING_MODEL", self.DEFAULT_EMBEDDING_MODEL)

    @staticmethod
    def _sanitize_response_json_schema(schema: Any) -> Any:
        """Reduce a Pydantic JSON schema to Gemini's supported response-json-schema subset."""
        if isinstance(schema, list):
            return [GeminiExecutor._sanitize_response_json_schema(item) for item in schema]
        if not isinstance(schema, dict):
            return schema

        if "$ref" in schema:
            # Gemini can follow schema references, but only if we drop sibling keys that Pydantic
            # may include alongside `$ref`.
            return {
                key: GeminiExecutor._sanitize_response_json_schema(value)
                for key, value in schema.items()
                if key.startswith("$")
            }

        sanitized: dict[str, Any] = {}
        property_names: list[str] = []
        for key, value in schema.items():
            if key not in SUPPORTED_JSON_SCHEMA_KEYS:
                continue
            if key in {"$defs", "properties"} and isinstance(value, dict):
                sanitized[key] = {
                    name: GeminiExecutor._sanitize_response_json_schema(subschema)
                    for name, subschema in value.items()
                }
                if key == "properties":
                    property_names = list(value.keys())
                continue
            if key in {"items", "additionalProperties"} and isinstance(value, dict):
                sanitized[key] = GeminiExecutor._sanitize_response_json_schema(value)
                continue
            if key in {"anyOf", "oneOf", "prefixItems"} and isinstance(value, list):
                sanitized[key] = [
                    GeminiExecutor._sanitize_response_json_schema(item) for item in value
                ]
                continue
            sanitized[key] = value

        if property_names and "propertyOrdering" not in sanitized:
            # Preserve declared field order so Gemini's JSON output is more predictable and easier
            # to diff in saved artifacts and tests.
            sanitized["propertyOrdering"] = property_names
        return sanitized

    @classmethod
    def _build_response_json_schema(cls, response_model: type[T]) -> dict[str, Any]:
        """Build a Gemini-compatible JSON schema for a Pydantic response model."""
        raw_schema = response_model.model_json_schema(by_alias=True)
        return cls._sanitize_response_json_schema(raw_schema)

    @staticmethod
    def _summarize_error(error: Exception | None, *, max_length: int = 1_200) -> str:
        """Return a compact error summary suitable for Temporal failure payloads."""
        if error is None:
            return "unknown error"
        message = " ".join(str(error).split()) or error.__class__.__name__
        if len(message) <= max_length:
            return message
        return f"{message[: max_length - 1]}…"

    def generate_structured(
        self,
        *,
        stage_name: str,
        prompt: str,
        response_model: type[T],
        system_instruction: str,
        attachments: list[GeminiAttachment] | None = None,
        temperature: float = 0.1,
        max_output_tokens: int = 8_192,
    ) -> T:
        """Run a structured generation request and parse it into `response_model`."""
        client = self._client_or_raise()
        model = self.resolve_generation_model(stage_name)
        # The prompt is always the first content part. Binary inputs are appended afterward so the
        # caller can keep prompt construction separate from attachment loading.
        content_parts: list[str | types.Part] = [prompt]
        for attachment in attachments or []:
            content_parts.append(
                types.Part.from_bytes(data=attachment.data, mime_type=attachment.mime_type)
            )
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
            # `response_json_schema` is used instead of `response_schema` because the sanitized dict
            # form is more stable across Pydantic models and Gemini SDK versions.
            response_json_schema=self._build_response_json_schema(response_model),
        )

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=content_parts,
                    config=config,
                )
                if response.parsed is not None:
                    # Prefer the SDK's parsed payload when available, but still validate it through
                    # Pydantic so callers get the exact response model type they requested.
                    if isinstance(response.parsed, response_model):
                        return response.parsed
                    return response_model.model_validate(response.parsed)
                if response.text:
                    # Some SDK responses omit `parsed` but still include raw JSON text.
                    # Keep that fallback so structured generation remains robust across model/SDK
                    # variations.
                    parsed_payload = json.loads(response.text)
                    return response_model.model_validate(parsed_payload)
                raise GeminiExecutorError("Gemini returned no parsed payload or text.")
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == self._max_attempts:
                    break
                # Simple bounded backoff smooths over transient API failures without stretching
                # Temporal activity duration too aggressively.
                time.sleep(min(attempt, 3))
        raise GeminiExecutorError(
            f"Gemini structured generation failed for stage '{stage_name}': "
            f"{self._summarize_error(last_error)}"
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
        client = self._client_or_raise()
        model = self.resolve_embedding_model()
        config = types.EmbedContentConfig(taskType=task_type, title=title)
        vectors: list[list[float]] = []

        try:
            # Gemini embedding APIs accept bounded batch sizes, so the executor centralizes batching
            # instead of forcing every retrieval caller to know the transport limit.
            for batch_start in range(0, len(texts), self.MAX_EMBED_BATCH_SIZE):
                batch = texts[batch_start : batch_start + self.MAX_EMBED_BATCH_SIZE]
                response = client.models.embed_content(
                    model=model,
                    contents=batch,
                    config=config,
                )
                vectors.extend(embedding.values for embedding in response.embeddings)
        except Exception as exc:  # noqa: BLE001
            raise GeminiExecutorError(
                "Gemini embedding failed: " f"{self._summarize_error(exc)}"
            ) from None
        return vectors
