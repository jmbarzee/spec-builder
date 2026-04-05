"""Prompt builders for spec builder Gemini-backed activities."""

from __future__ import annotations

from typing import Any

from src.workflows.spec_builder.custom_types import (
    AcquisitionOutput,
    ArtifactInput,
    BestPracticesPassage,
    BestPracticesReviewOutput,
    ClarificationRecord,
    CriticOutput,
    DraftSpecOutput,
    IntentClassificationOutput,
    StructuredExtractionOutput,
)


def _json_payload(value: Any) -> str:
    """Serialize prompt context consistently."""
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json(indent=2, by_alias=True)
    raise TypeError(f"Unsupported prompt payload type: {type(value)!r}")


def _bullet_list(values: list[str]) -> str:
    """Render a prompt-friendly bullet list."""
    if not values:
        return "- none"
    return "\n".join(f"- {value}" for value in values if value) or "- none"


def _clarification_lines(clarifications: list[ClarificationRecord]) -> str:
    """Render clarifications for prompt context."""
    rendered = [
        f"- clarification-{entry.request_id}: {entry.clarification}"
        for entry in clarifications
        if entry.clarification
    ]
    return "\n".join(rendered) or "- none"


def build_acquisition_system_instruction() -> str:
    """Return the acquisition stage system instruction."""
    return (
        "You normalize source artifacts into a replay-safe acquisition bundle for a Temporal "
        "spec builder. Extract only what is present in the file. Separate observable visual "
        "structure from interpretation. If content is unclear, lower confidence and add a "
        "warning instead of inventing detail."
    )


def build_acquisition_prompt(
    artifact: ArtifactInput,
    clarifications: list[ClarificationRecord],
) -> str:
    """Return the acquisition prompt for one artifact."""
    clarification_text = _clarification_lines(clarifications)
    return f"""
Normalize the attached artifact into the `AcquiredArtifact` schema.

Rules:
- Preserve source fidelity.
- Extract text verbatim when possible.
- For diagrams or screenshots, describe visible nodes, edges, labels, and groups only.
- Keep confidence between 0 and 1.
- Put uncertainty in `warnings`.
- Do not invent missing labels or control flow.

Artifact metadata:
{_json_payload(artifact)}

Human clarifications already available for this submission:
{clarification_text}
""".strip()


def build_structured_extraction_system_instruction() -> str:
    """Return the extraction stage system instruction."""
    return (
        "You convert normalized source material into a structured intermediate representation "
        "for an implementation-ready Temporal specification. Capture entities, relationships, "
        "facts, and ambiguities with explicit source traceability. Mark unsupported reasoning "
        "as inferred, not explicit."
    )


def build_structured_extraction_prompt(
    acquisition: AcquisitionOutput,
    clarifications: list[ClarificationRecord],
    revision_notes: list[str],
    previous_extraction: StructuredExtractionOutput | None,
) -> str:
    """Return the structured extraction prompt."""
    previous_extraction_payload = (
        _json_payload(previous_extraction) if previous_extraction is not None else "null"
    )
    return f"""
Transform the acquisition bundle into `StructuredExtractionOutput`.

Requirements:
- Extract concrete workflows, activities, states, inputs, outputs, APIs, loops, constraints, and components.
- Every entity, relationship, and fact must reference one or more source artifact ids.
- Facts that are not directly stated must be marked as `inferred`.
- Ambiguities must be short, actionable questions or gaps.
- Avoid generic filler like "system" or "process" unless the source explicitly uses that term.
- When clarifications introduce new facts, use source artifact ids like `clarification-<request_id>`.
- When a previous extraction is provided, revise it instead of discarding correct structure.

Acquisition bundle:
{_json_payload(acquisition)}

Human clarifications:
{_clarification_lines(clarifications)}

Revision notes:
{_bullet_list(revision_notes)}

Previous extraction:
{previous_extraction_payload}
""".strip()


def build_intent_classification_system_instruction() -> str:
    """Return the intent classification system instruction."""
    return (
        "You classify the target output for a spec builder. Choose the most specific primary "
        "intent and target audience supported by the extracted evidence. Use `temporal_workflow` "
        "as the best-practices profile when the source describes Temporal workflow orchestration."
    )


def build_intent_classification_prompt(
    extraction: StructuredExtractionOutput,
    user_intent: str | None,
) -> str:
    """Return the intent classification prompt."""
    stated_intent = user_intent or "No explicit user intent was provided."
    return f"""
Produce `IntentClassificationOutput`.

Rules:
- Base the answer on extracted evidence first.
- Use the explicit user intent only when it does not conflict with the evidence.
- Confidence should drop if multiple outputs are plausible.

User intent:
{stated_intent}

Structured extraction:
{_json_payload(extraction)}
""".strip()


def build_extraction_critic_system_instruction() -> str:
    """Return the critic stage system instruction."""
    return (
        "You are the extraction critic for a Temporal spec builder. Judge whether the current "
        "understanding is strong enough to draft from. Recommend `reextract` only for "
        "fundamental understanding gaps, and recommend `ask_human` when human clarification "
        "is the only realistic way to unblock ambiguity."
    )


def build_extraction_critic_prompt(
    extraction: StructuredExtractionOutput,
    intent: IntentClassificationOutput,
) -> str:
    """Return the critic prompt."""
    return f"""
Produce `CriticOutput`.

Scoring guidance:
- 0.9 to 1.0: extraction is ready to draft.
- 0.6 to 0.89: mostly strong, but there are non-blocking issues.
- below 0.6: there are blocking issues or major missing information.

Decision guidance:
- `proceed` when drafting can happen safely.
- `reextract` when the extracted representation is missing core flow or components.
- `ask_human` when the source itself is too ambiguous and another extraction pass is unlikely to fix it.

Structured extraction:
{_json_payload(extraction)}

Intent classification:
{_json_payload(intent)}
""".strip()


def build_draft_spec_system_instruction() -> str:
    """Return the draft generation system instruction."""
    return (
        "You draft an implementation-ready specification for engineers and coding agents. "
        "The output must be high-signal, concrete, and traceable. Separate facts, assumptions, "
        "and open questions. Do not claim requirements that are not grounded in the extracted "
        "source unless clearly marked as `proposed_extension` traceability."
    )


def build_draft_spec_prompt(
    extraction: StructuredExtractionOutput,
    intent: IntentClassificationOutput,
    clarifications: list[ClarificationRecord],
    revision_notes: list[str],
    previous_draft: DraftSpecOutput | None,
    previous_best_practices_review: BestPracticesReviewOutput | None,
) -> str:
    """Return the draft prompt."""
    previous_draft_payload = _json_payload(previous_draft) if previous_draft is not None else "null"
    previous_review_payload = (
        _json_payload(previous_best_practices_review)
        if previous_best_practices_review is not None
        else "null"
    )
    return f"""
Produce `DraftSpecOutput`.

Markdown requirements:
- Use a strong title.
- Include objective, scope, architecture or execution flow, data contracts, error handling, observability, assumptions, and open questions.
- Write for downstream implementation, not for executives.

Structured JSON requirements:
- Fill every `StructuredSpec` field.
- `data_contracts` must summarize key typed contracts or payloads.
- `traceability` must map each important claim to source artifact ids and classify it as `explicit`, `inferred`, or `proposed_extension`.
- When revising an existing draft, preserve sections that are already correct.
- Explicitly address every high-severity issue from the previous best-practices review.
- When clarifications introduce new facts, use source artifact ids like `clarification-<request_id>`.

Extraction:
{_json_payload(extraction)}

Intent classification:
{_json_payload(intent)}

Human clarifications:
{_clarification_lines(clarifications)}

Revision notes:
{_bullet_list(revision_notes)}

Previous draft:
{previous_draft_payload}

Previous best-practices review:
{previous_review_payload}
""".strip()


def build_best_practices_review_system_instruction() -> str:
    """Return the best-practices review system instruction."""
    return (
        "You review a draft spec against Temporal best practices. Focus on determinism, "
        "activity boundaries, child workflow usage, parent close policy, signal/query design, "
        "continue-as-new strategy, idempotency, retry guidance, and event history growth."
    )


def build_best_practices_review_prompt(
    draft: DraftSpecOutput,
    passages: list[BestPracticesPassage],
) -> str:
    """Return the review prompt."""
    rendered_passages = "\n\n".join(
        f"[{passage.passage_id}] score={passage.relevance_score:.3f}\n{passage.text}"
        for passage in passages
    )
    return f"""
Produce `BestPracticesReviewOutput`.

Rules:
- Use only the supplied passages as platform guidance.
- Flag concrete violations and weak recommendations.
- `pass_with_warnings` is valid when the draft is usable but still has medium/low issues.
- `revise` is required when there are high-severity Temporal correctness or operability issues.

Draft spec:
{_json_payload(draft)}

Relevant Temporal guidance:
{rendered_passages}
""".strip()


def build_supervisor_decision_system_instruction() -> str:
    """Return the supervisor system instruction."""
    return (
        "You are the bounded supervisor for a fixed Temporal spec-builder pipeline. "
        "You may choose only `accept`, `revise_from_extraction`, `revise_from_draft`, "
        "or `ask_human`. You do not rewrite the draft. You choose the smallest restart "
        "that safely addresses the issues."
    )


def build_supervisor_decision_prompt(
    extraction_critic: CriticOutput,
    draft: DraftSpecOutput,
    best_practices_review: BestPracticesReviewOutput,
    iteration: int,
) -> str:
    """Return the supervisor prompt."""
    return f"""
Produce `SupervisorDecision`.

Policy:
- Use `revise_from_extraction` only when understanding is fundamentally wrong.
- Use `revise_from_draft` when the understanding is sound but the draft or review needs another pass.
- Use `ask_human` when ambiguity remains unresolved after the available evidence.
- Use `accept` only when the draft is implementation-ready enough for a human approver.

Current iteration:
{iteration}

Extraction critic:
{_json_payload(extraction_critic)}

Draft:
{_json_payload(draft)}

Best-practices review:
{_json_payload(best_practices_review)}
""".strip()


def summarize_review_for_status(review: BestPracticesReviewOutput | None) -> str | None:
    """Return a short human-readable summary for status panels."""
    if review is None:
        return None
    return f"{review.verdict} with {len(review.violations)} issue(s)"
