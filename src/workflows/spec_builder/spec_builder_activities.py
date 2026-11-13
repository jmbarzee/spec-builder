"""Activities for the spec builder workflow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import mimetypes
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

from pydantic import Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.workflows.spec_builder.custom_types import (
    AcquisitionOutput,
    AcquiredArtifact,
    AcquireContentInput,
    ArtifactInput,
    BestPracticesPassage,
    BestPracticesReviewOutput,
    BestPracticesVerdict,
    BestPracticesViolation,
    ClarificationRecord,
    CriticOutput,
    DraftSpecInput,
    DraftSpecOutput,
    ExtractionCriticInput,
    IntentClassificationInput,
    IntentClassificationOutput,
    PersistFinalOutputsInput,
    PersistFinalOutputsOutput,
    RunBestPracticesReviewActivityInput,
    StrictModel,
    StructuredExtractionInput,
    StructuredExtractionOutput,
    SupervisorDecision,
    SupervisorDecisionInput,
    TemporalBestPracticesReviewInput,
    TraceabilitySummary,
    TwfDiagnostic,
    ValidateTwfInput,
    ValidateTwfOutput,
)
from src.workflows.spec_builder.gemini_executor import GeminiAttachment, GeminiExecutor
from src.workflows.spec_builder.prompts.activity_prompts import (
    build_acquisition_prompt,
    build_acquisition_system_instruction,
    build_best_practices_review_prompt,
    build_best_practices_review_system_instruction,
    build_draft_spec_prompt,
    build_draft_spec_system_instruction,
    build_extraction_critic_prompt,
    build_extraction_critic_system_instruction,
    build_intent_classification_prompt,
    build_intent_classification_system_instruction,
    build_structured_extraction_prompt,
    build_structured_extraction_system_instruction,
    build_supervisor_decision_prompt,
    build_supervisor_decision_system_instruction,
)

RUNTIME_ROOT = Path(".spec_builder_runtime")
DEFAULT_CORPUS_PATH = Path("src/resources/sa_workflow_design_best_practices_critic_guide.md")

class ReviewAssessment(StrictModel):
    """Internal response shape for the review model."""

    violations: list[BestPracticesViolation] = Field(default_factory=list)
    verdict: BestPracticesVerdict

def _runtime_dir(job_id: str) -> Path:
    # Each activity writes deterministic-by-name artifacts under one job-scoped runtime tree so the
    # workflow and GUI can refer back to intermediate files by URI.
    path = RUNTIME_ROOT / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path

def _stage_dir(job_id: str, name: str) -> Path:
    path = _runtime_dir(job_id) / name
    path.mkdir(parents=True, exist_ok=True)
    return path

def _uri_for_path(path: Path) -> str:
    return path.resolve().as_uri()

def _path_from_uri(uri: str) -> Path:
    # Activities intentionally only accept local paths today. That keeps acquisition and final
    # persistence side effects explicit and easy to reason about in tests.
    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        target = unquote(parsed.path) if parsed.scheme == "file" else uri
        return Path(target).expanduser().resolve()
    raise ValueError(f"Only local file paths are supported. Received: {uri}")

def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return normalized or "artifact"

def _json_write(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        # Prefer model-aware serialization so aliased Pydantic fields survive exactly as downstream
        # prompts and clients expect them.
        if hasattr(payload, "model_dump"):
            json.dump(payload.model_dump(by_alias=True), handle, indent=2)
        else:
            json.dump(payload, handle, indent=2)
    return _uri_for_path(path)

def _text_write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return _uri_for_path(path)

def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped

def _checksum_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def _looks_like_text(data: bytes) -> bool:
    if not data:
        return True
    # This is a cheap guard, not a MIME detector. We only need to know whether local decoding is
    # likely safe before falling back to multimodal extraction.
    sample = data[:4_096]
    if b"\x00" in sample:
        return False
    printable = sum(
        1 for byte in sample if byte in b"\n\r\t" or 32 <= byte <= 126 or 128 <= byte <= 255
    )
    return printable / len(sample) > 0.85

def _detect_mime_type(artifact: ArtifactInput, path: Path) -> str:
    if artifact.mime_type:
        return artifact.mime_type
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"

def _read_text_if_available(path: Path) -> str | None:
    data = path.read_bytes()
    if not _looks_like_text(data):
        return None
    return data.decode("utf-8", errors="replace").strip()

def _is_visual_or_binary(artifact: ArtifactInput, mime_type: str) -> bool:
    return (
        artifact.type in {"diagram", "image", "slides"}
        or mime_type.startswith("image/")
        or mime_type == "application/pdf"
    )

def _build_acquired_artifact_from_text(
    artifact: ArtifactInput,
    source_uri: str,
    text: str,
) -> AcquiredArtifact:
    return AcquiredArtifact(
        artifact_id=artifact.artifact_id,
        type=artifact.type,
        extracted_text=text,
        confidence=0.98,
        warnings=[],
        source_uri=source_uri,
    )

def _merge_acquired_artifacts(
    artifact: ArtifactInput,
    source_uri: str,
    local_result: AcquiredArtifact | None,
    gemini_result: AcquiredArtifact | None,
) -> AcquiredArtifact:
    # Local text extraction is fast and lossless for readable files. Gemini adds OCR / multimodal
    # coverage for PDFs and images. We preserve both when available instead of choosing one winner.
    extracted_text_parts = [
        value
        for value in [
            local_result.extracted_text if local_result else None,
            gemini_result.extracted_text if gemini_result else None,
        ]
        if value
    ]
    merged_text = "\n\n".join(_dedupe_strings(extracted_text_parts)) or None
    warnings = _dedupe_strings(
        [
            *(local_result.warnings if local_result else []),
            *(gemini_result.warnings if gemini_result else []),
        ]
    )
    visual_elements = gemini_result.visual_elements if gemini_result else []
    confidence = max(
        local_result.confidence if local_result else 0.0,
        gemini_result.confidence if gemini_result else 0.0,
    )
    return AcquiredArtifact(
        artifact_id=artifact.artifact_id,
        type=artifact.type,
        extracted_text=merged_text,
        visual_elements=visual_elements,
        confidence=confidence,
        warnings=warnings,
        source_uri=source_uri,
    )

def _load_executor() -> GeminiExecutor:
    return GeminiExecutor()

def _extract_with_gemini(
    *,
    artifact: ArtifactInput,
    raw_path: Path,
    clarifications: list[ClarificationRecord],
) -> AcquiredArtifact:
    executor = _load_executor()
    mime_type = _detect_mime_type(artifact, raw_path)
    acquired = executor.generate_structured(
        stage_name="acquire_content",
        # Clarifications are threaded into acquisition so the model can reinterpret the same
        # source material with the user's added context instead of treating clarifications as a
        # later patch.
        prompt=build_acquisition_prompt(artifact, clarifications),
        response_model=AcquiredArtifact,
        system_instruction=build_acquisition_system_instruction(),
        attachments=[
            GeminiAttachment(
                data=raw_path.read_bytes(),
                mime_type=mime_type,
                filename=raw_path.name,
            )
        ],
        temperature=0.0,
    )
    return acquired.model_copy(
        update={
            "artifact_id": artifact.artifact_id,
            "type": artifact.type,
            "source_uri": _uri_for_path(raw_path),
        }
    )

def _persist_normalized_text(job_id: str, artifact: AcquiredArtifact) -> AcquiredArtifact:
    if not artifact.extracted_text:
        return artifact
    normalized_dir = _stage_dir(job_id, "normalized")
    normalized_path = normalized_dir / f"{_slug(artifact.artifact_id)}.text.md"
    normalized_uri = _text_write(normalized_path, artifact.extracted_text)
    return artifact.model_copy(update={"normalized_text_uri": normalized_uri})

def _persist_clarification_note(job_id: str, clarification: ClarificationRecord) -> ClarificationRecord:
    clarifications_dir = _stage_dir(job_id, "clarifications")
    note_path = clarifications_dir / f"clarification-{_slug(clarification.request_id)}.md"
    note_text = f"# Clarification\n\n{clarification.clarification.strip()}\n"
    note_uri = _text_write(note_path, note_text)
    return clarification.model_copy(update={"note_uri": note_uri})

def _clarification_artifact(clarification: ClarificationRecord) -> AcquiredArtifact:
    # Clarifications are promoted into pseudo-artifacts so later stages can consume them through the
    # same artifact-oriented prompts they already use for uploaded source material.
    return AcquiredArtifact(
        artifact_id=f"clarification-{clarification.request_id}",
        type="notes",
        extracted_text=clarification.clarification,
        confidence=1.0,
        warnings=[],
        source_uri=clarification.note_uri,
        normalized_text_uri=clarification.note_uri,
    )

def _render_traceability_summary(draft: DraftSpecOutput) -> TraceabilitySummary:
    # The final summary highlights claims/questions still lacking an explicit traceability mapping,
    # which is easier for the GUI to display than scanning the full structured draft payload.
    uncovered_claims = [
        question
        for question in draft.open_questions
        if question
        and question
        not in {entry.claim for entry in draft.structured_spec.traceability if entry.claim}
    ]
    return TraceabilitySummary(
        entries=draft.structured_spec.traceability,
        uncovered_claims=_dedupe_strings(uncovered_claims),
    )


def _should_auto_accept(
    critic: CriticOutput,
    review: BestPracticesReviewOutput,
    decision: SupervisorDecision,
) -> bool:
    """Return whether low-risk review output should be accepted without another rewrite."""
    has_high_risk_violation = any(
        violation.severity == "high" for violation in review.violations
    )
    return (
        decision.action in {"revise_from_draft", "revise_from_extraction"}
        and critic.recommended_action == "proceed"
        and not critic.blocking_issues
        and review.verdict == "pass_with_warnings"
        and not has_high_risk_violation
    )


def _chunk_markdown_by_heading(text: str) -> list[tuple[str, str]]:
    # Passage retrieval works better when embeddings respect section boundaries instead of embedding
    # one monolithic corpus document.
    chunks: list[tuple[str, str]] = []
    current_heading = "Introduction"
    current_lines: list[str] = []
    for line in text.splitlines():
        if re.match(r"^#{1,6}\s+", line):
            if current_lines:
                chunk_text = "\n".join(current_lines).strip()
                if chunk_text:
                    chunks.append((current_heading, chunk_text))
            current_heading = re.sub(r"^#{1,6}\s+", "", line).strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        chunk_text = "\n".join(current_lines).strip()
        if chunk_text:
            chunks.append((current_heading, chunk_text))
    return chunks


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _load_corpus_text(corpus_uri: str | None) -> str:
    path = _path_from_uri(corpus_uri) if corpus_uri else DEFAULT_CORPUS_PATH.resolve()
    return path.read_text(encoding="utf-8")


@activity.defn
async def acquire_content(input: AcquireContentInput) -> AcquisitionOutput:
    """Produce the normalized acquisition bundle for the submission."""
    activity.logger.info("Acquire content for job %s", input.job_id)
    raw_dir = _stage_dir(input.job_id, "raw")
    acquisition_dir = _stage_dir(input.job_id, "acquisition")

    # Clarifications are persisted immediately so subsequent activities can refer to durable note
    # URIs instead of only in-memory signal payloads.
    clarification_records = [
        _persist_clarification_note(input.job_id, clarification) for clarification in input.clarifications
    ]

    # Uploaded artifacts and clarification-attached artifacts share one deduped map keyed by
    # `artifact_id` so the workflow sees a single normalized acquisition bundle.
    artifact_map: dict[str, ArtifactInput] = {artifact.artifact_id: artifact for artifact in input.artifacts}
    for clarification in clarification_records:
        for artifact in clarification.artifact_refs:
            artifact_map.setdefault(artifact.artifact_id, artifact)

    acquired_artifacts: list[AcquiredArtifact] = []
    warnings: list[str] = []

    for artifact in artifact_map.values():
        source_path = _path_from_uri(artifact.uri)
        if not source_path.exists():
            warnings.append(f"Artifact '{artifact.artifact_id}' does not exist at {artifact.uri}.")
            continue

        # Copy every source into runtime storage first so downstream URIs remain stable even if the
        # original user-supplied file later moves or is deleted.
        target_name = f"{_slug(artifact.artifact_id)}{source_path.suffix or '.bin'}"
        raw_path = raw_dir / target_name
        shutil.copyfile(source_path, raw_path)
        source_uri = _uri_for_path(raw_path)

        local_result: AcquiredArtifact | None = None
        gemini_result: AcquiredArtifact | None = None
        local_text = _read_text_if_available(raw_path)
        if local_text:
            local_result = _build_acquired_artifact_from_text(artifact, source_uri, local_text)

        mime_type = _detect_mime_type(artifact, raw_path)
        if _is_visual_or_binary(artifact, mime_type):
            # Visual artifacts can still produce textual evidence through Gemini, so we merge that
            # output with any locally-readable text instead of treating the strategies as exclusive.
            gemini_result = _extract_with_gemini(
                artifact=artifact,
                raw_path=raw_path,
                clarifications=clarification_records,
            )

        if local_result is None and gemini_result is None:
            warnings.append(
                f"Artifact '{artifact.artifact_id}' could not be normalized as text or multimodal input."
            )
            acquired = AcquiredArtifact(
                artifact_id=artifact.artifact_id,
                type=artifact.type,
                confidence=0.0,
                warnings=["No readable content was extracted."],
                source_uri=source_uri,
            )
        else:
            acquired = _merge_acquired_artifacts(artifact, source_uri, local_result, gemini_result)
        acquired_artifacts.append(_persist_normalized_text(input.job_id, acquired))

    for clarification in clarification_records:
        acquired_artifacts.append(_clarification_artifact(clarification))

    bundle = AcquisitionOutput(
        source_bundle_id=f"source-bundle-{input.job_id}",
        artifacts=acquired_artifacts,
        warnings=_dedupe_strings(warnings),
    )
    bundle_uri = _json_write(acquisition_dir / "source-bundle.json", bundle)
    return bundle.model_copy(update={"source_bundle_uri": bundle_uri})


@activity.defn
async def run_structured_extraction(input: StructuredExtractionInput) -> StructuredExtractionOutput:
    """Generate the structured intermediate representation."""
    activity.logger.info("Run structured extraction for job %s", input.job_id)
    executor = _load_executor()
    extraction = executor.generate_structured(
        stage_name="structured_extraction",
        # Prior extraction output and revision notes are fed back in to support targeted rewrites
        # instead of forcing the model to rediscover the entire intermediate representation.
        prompt=build_structured_extraction_prompt(
            input.acquisition,
            input.clarifications,
            input.revision_notes,
            input.previous_extraction,
        ),
        response_model=StructuredExtractionOutput,
        system_instruction=build_structured_extraction_system_instruction(),
    )
    extraction_dir = _stage_dir(input.job_id, "extraction")
    artifact_uri = _json_write(extraction_dir / "structured-extraction.json", extraction)
    return extraction.model_copy(update={"artifact_uri": artifact_uri})


@activity.defn
async def run_intent_classification(
    input: IntentClassificationInput,
) -> IntentClassificationOutput:
    """Classify the target output intent for the submission."""
    activity.logger.info("Run intent classification for job %s", input.job_id)
    executor = _load_executor()
    intent = executor.generate_structured(
        stage_name="intent_classification",
        prompt=build_intent_classification_prompt(input.extraction, input.user_intent),
        response_model=IntentClassificationOutput,
        system_instruction=build_intent_classification_system_instruction(),
    )
    classification_dir = _stage_dir(input.job_id, "review")
    artifact_uri = _json_write(classification_dir / "intent-classification.json", intent)
    return intent.model_copy(update={"artifact_uri": artifact_uri})


@activity.defn
async def run_extraction_critic(input: ExtractionCriticInput) -> CriticOutput:
    """Assess whether the current extraction is strong enough to draft from."""
    activity.logger.info("Run extraction critic for job %s", input.job_id)
    executor = _load_executor()
    critic = executor.generate_structured(
        stage_name="extraction_critic",
        prompt=build_extraction_critic_prompt(input.extraction, input.intent),
        response_model=CriticOutput,
        system_instruction=build_extraction_critic_system_instruction(),
    )
    review_dir = _stage_dir(input.job_id, "review")
    artifact_uri = _json_write(review_dir / "extraction-critic.json", critic)
    return critic.model_copy(update={"artifact_uri": artifact_uri})


@activity.defn
async def run_draft_spec(input: DraftSpecInput) -> DraftSpecOutput:
    """Draft the markdown spec and structured JSON companion."""
    activity.logger.info("Run draft generation for job %s", input.job_id)
    executor = _load_executor()
    draft = executor.generate_structured(
        stage_name="draft_spec",
        prompt=build_draft_spec_prompt(
            input.extraction,
            input.intent,
            input.clarifications,
            input.revision_notes,
            input.previous_draft,
            input.previous_best_practices_review,
        ),
        response_model=DraftSpecOutput,
        system_instruction=build_draft_spec_system_instruction(),
    )
    # The model may fill assumptions/questions in either the top-level fields or the structured
    # payload. Normalize them here so downstream workflow logic has one canonical view.
    assumptions = draft.assumptions or draft.structured_spec.assumptions
    open_questions = draft.open_questions or draft.structured_spec.open_questions
    summary = draft.summary or draft.structured_spec.objective
    next_draft_version = (
        input.previous_draft.draft_version + 1
        if input.previous_draft is not None
        else draft.draft_version
    )
    draft = draft.model_copy(
        update={
            "assumptions": _dedupe_strings(assumptions),
            "open_questions": _dedupe_strings(open_questions),
            "summary": summary,
            # Preserve monotonic versioning across revisions even if the model emits an older
            # version number than the workflow already knows about.
            "draft_version": max(draft.draft_version, next_draft_version),
        }
    )
    drafts_dir = _stage_dir(input.job_id, "drafts")
    markdown_uri = _text_write(
        drafts_dir / f"spec.v{draft.draft_version}.md",
        draft.markdown_spec,
    )
    structured_uri = _json_write(
        drafts_dir / f"spec.v{draft.draft_version}.json",
        draft.structured_spec,
    )
    return draft.model_copy(
        update={
            "markdown_spec_uri": markdown_uri,
            "structured_spec_uri": structured_uri,
        }
    )

@activity.defn
async def retrieve_best_practices_passages(
    input: TemporalBestPracticesReviewInput,
) -> list[BestPracticesPassage]:
    """Retrieve the most relevant best-practices passages for the draft."""
    activity.logger.info("Retrieve best-practices passages for job %s", input.job_id)
    corpus_text = _load_corpus_text(input.corpus_uri)
    checksum = _checksum_bytes(corpus_text.encode("utf-8"))
    corpus_dir = _stage_dir(input.job_id, "corpus")
    cache_path = corpus_dir / f"{checksum}.embeddings.json"
    chunks = _chunk_markdown_by_heading(corpus_text)
    executor = _load_executor()

    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        chunk_records = cached["chunks"]
    else:
        # Cache document embeddings by corpus checksum so repeated review passes for the same corpus
        # only pay the query-embedding cost.
        embeddings = executor.embed_texts(
            [chunk_text for _, chunk_text in chunks],
            task_type="RETRIEVAL_DOCUMENT",
            title="Temporal workflow best practices",
        )
        chunk_records = [
            {
                "passage_id": f"passage-{index + 1:03d}",
                "title": title,
                "text": chunk_text,
                "embedding": embedding,
            }
            for index, ((title, chunk_text), embedding) in enumerate(zip(chunks, embeddings, strict=False))
        ]
        cache_path.write_text(json.dumps({"chunks": chunk_records}, indent=2), encoding="utf-8")

    # Retrieve against the full markdown draft rather than a summary so ranking favors passages that
    # match the actual spec language the reviewer will inspect.
    query_text = input.draft.markdown_spec
    query_embedding = executor.embed_texts([query_text], task_type="RETRIEVAL_QUERY")[0]
    ranked = sorted(
        (
            BestPracticesPassage(
                passage_id=record["passage_id"],
                text=record["text"],
                relevance_score=_cosine_similarity(query_embedding, record["embedding"]),
            )
            for record in chunk_records
        ),
        key=lambda passage: passage.relevance_score,
        reverse=True,
    )
    selected = ranked[: min(5, len(ranked))]
    review_dir = _stage_dir(input.job_id, "review")
    _json_write(review_dir / "retrieved-passages.json", [passage.model_dump() for passage in selected])
    return selected


@activity.defn
async def run_best_practices_review(
    input: RunBestPracticesReviewActivityInput,
) -> BestPracticesReviewOutput:
    """Review the draft against retrieved Temporal best-practices passages."""
    activity.logger.info("Run best-practices review for job %s", input.job_id)
    executor = _load_executor()
    assessment = executor.generate_structured(
        stage_name="best_practices_review",
        # Retrieval and reasoning are separated: passage selection happens in one activity, then the
        # model judges the draft only against the most relevant supporting passages.
        prompt=build_best_practices_review_prompt(input.draft, input.passages),
        response_model=ReviewAssessment,
        system_instruction=build_best_practices_review_system_instruction(),
    )
    review = BestPracticesReviewOutput(
        retrieved_passages=input.passages,
        violations=assessment.violations,
        verdict=assessment.verdict,
    )
    review_dir = _stage_dir(input.job_id, "review")
    artifact_uri = _json_write(review_dir / "best-practices-review.json", review)
    return review.model_copy(update={"artifact_uri": artifact_uri})


@activity.defn
async def run_supervisor_decision(input: SupervisorDecisionInput) -> SupervisorDecision:
    """Choose the bounded next action for the pipeline."""
    activity.logger.info("Run supervisor decision for job %s", input.job_id)
    executor = _load_executor()
    decision = executor.generate_structured(
        stage_name="supervisor_decision",
        prompt=build_supervisor_decision_prompt(
            input.extraction_critic,
            input.draft,
            input.best_practices_review,
            input.iteration,
        ),
        response_model=SupervisorDecision,
        system_instruction=build_supervisor_decision_system_instruction(),
    )
    if decision.action == "ask_human" and not decision.questions:
        # If the model asks for human input without concrete questions, synthesize a small bounded
        # list from the current draft/review state so the UI always has actionable prompts.
        decision = decision.model_copy(
            update={
                "questions": _dedupe_strings(
                    input.draft.open_questions
                    + input.extraction_critic.missing_information
                    + [violation.issue for violation in input.best_practices_review.violations]
                )[:5]
            }
        )
    elif _should_auto_accept(input.extraction_critic, input.best_practices_review, decision):
        # Keep the activity-side heuristic aligned with the workflow's acceptance shortcut so tests
        # and single-activity callers observe the same decision boundary.
        decision = decision.model_copy(
            update={
                "action": "accept",
                "reason": (
                    decision.reason
                    or "No high-severity best-practices issues remain after review."
                ),
                "questions": [],
            }
        )
    review_dir = _stage_dir(input.job_id, "review")
    artifact_uri = _json_write(review_dir / "supervisor-decision.json", decision)
    return decision.model_copy(update={"artifact_uri": artifact_uri})


@activity.defn
async def persist_final_outputs(
    input: PersistFinalOutputsInput,
) -> PersistFinalOutputsOutput:
    """Materialize final artifacts into runtime storage and the requested output directory."""
    activity.logger.info("Persist final outputs for job %s", input.job_id)
    final_dir = _stage_dir(input.job_id, "final")
    output_dir = Path(input.output_directory).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # The runtime copy is the workflow-owned audit trail. The output-directory copy is the
    # user-facing deliverable that external tooling can consume directly.
    traceability_summary = _render_traceability_summary(input.draft)
    issues_payload = {
        "open_questions": input.open_questions,
        "warnings": input.warnings,
        "review_violations": (
            [violation.model_dump() for violation in input.best_practices_review.violations]
            if input.best_practices_review
            else []
        ),
    }

    runtime_spec_path = final_dir / "spec.md"
    runtime_spec_json_path = final_dir / "spec.json"
    runtime_traceability_path = final_dir / "traceability.json"
    runtime_review_path = final_dir / "best_practices_review.json"
    runtime_issues_path = final_dir / "issues.json"

    _text_write(runtime_spec_path, input.draft.markdown_spec)
    _json_write(runtime_spec_json_path, input.draft.structured_spec)
    _json_write(runtime_traceability_path, traceability_summary)
    _json_write(runtime_issues_path, issues_payload)
    if input.best_practices_review:
        _json_write(runtime_review_path, input.best_practices_review)

    spec_path = output_dir / "spec.md"
    spec_json_path = output_dir / "spec.json"
    traceability_path = output_dir / "traceability.json"
    review_path = output_dir / "best_practices_review.json"
    issues_path = output_dir / "issues.json"

    spec_path.write_text(input.draft.markdown_spec, encoding="utf-8")
    spec_json_path.write_text(
        input.draft.structured_spec.model_dump_json(indent=2, by_alias=True),
        encoding="utf-8",
    )
    traceability_path.write_text(
        traceability_summary.model_dump_json(indent=2, by_alias=True),
        encoding="utf-8",
    )
    issues_path.write_text(json.dumps(issues_payload, indent=2), encoding="utf-8")
    artifact_uris = {
        "spec_md": _uri_for_path(spec_path),
        "spec_json": _uri_for_path(spec_json_path),
        "traceability_json": _uri_for_path(traceability_path),
        "issues_json": _uri_for_path(issues_path),
    }
    if input.best_practices_review:
        review_path.write_text(
            input.best_practices_review.model_dump_json(indent=2, by_alias=True),
            encoding="utf-8",
        )
        artifact_uris["best_practices_review_json"] = _uri_for_path(review_path)

    return PersistFinalOutputsOutput(
        artifact_uris=artifact_uris,
        output_directory=str(output_dir),
    )

async def _run_twf(*args: str) -> tuple[int, str, str]:
    twf = shutil.which("twf")
    if twf is None:
        raise ApplicationError(
            "The `twf` CLI is not installed. Install it with `uv sync --extra architecture`.",
            non_retryable=True,
        )
    process = await asyncio.create_subprocess_exec(
        twf, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        process.kill()
        raise
    return process.returncode or 0, stdout.decode(), stderr.decode()


async def _twf_envelope(command: str, *args: str) -> str:
    # `parse` and `graph --json` exit 0 on a design with errors; a non-zero exit is the tool
    # failing, and the same design fails the same way again, so it is not retried.
    code, stdout, stderr = await _run_twf(command, *args)
    if code:
        raise ApplicationError(
            f"`twf {command}` failed with exit {code}: {stderr.strip()}", non_retryable=True
        )
    return stdout


@activity.defn
async def validate_twf_design(input: ValidateTwfInput) -> ValidateTwfOutput:
    """Format a `.twf` design, check it with `twf parse`, and record its parse and graph output."""
    activity.logger.info("Validate twf design for job %s", input.job_id)
    twf_dir = _stage_dir(input.job_id, "twf")
    source_path = twf_dir / "design.twf"
    _text_write(source_path, input.source)

    # `twf fmt` fixes space indentation. It exits non-zero on indentation it cannot read, which
    # `twf parse` then reports as a diagnostic, so its exit code is not a failure here.
    await _run_twf("fmt", str(source_path))
    parse_output = await _twf_envelope("parse", str(source_path))
    graph_output = await _twf_envelope("graph", "--json", str(source_path))

    diagnostics = [
        TwfDiagnostic(
            severity=finding["severity"],
            code=finding["code"],
            line=finding["start"]["line"],
            column=finding["start"]["column"],
            message=finding["message"],
        )
        for finding in json.loads(parse_output)["diagnostics"]
    ]
    return ValidateTwfOutput(
        source=source_path.read_text(encoding="utf-8"),
        valid=all(finding.severity != "error" for finding in diagnostics),
        diagnostics=diagnostics,
        artifact_uris={
            "twf_source": _uri_for_path(source_path),
            "twf_parse_json": _text_write(twf_dir / "parse.json", parse_output),
            "twf_graph_json": _text_write(twf_dir / "graph.json", graph_output),
        },
    )


SPEC_BUILDER_ACTIVITIES = [
    acquire_content,
    run_structured_extraction,
    run_intent_classification,
    run_extraction_critic,
    run_draft_spec,
    retrieve_best_practices_passages,
    run_best_practices_review,
    run_supervisor_decision,
    persist_final_outputs,
    validate_twf_design,
]
