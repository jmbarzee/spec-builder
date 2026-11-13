# Temporal Python SDK Project Template

![GitHub CI](https://github.com/kawofong/temporal-python-template/actions/workflows/ci.yml/badge.svg)
[![Code Coverage](https://img.shields.io/codecov/c/github/kawofong/temporal-python-template.svg?maxAge=86400)](https://codecov.io/github/kawofong/temporal-python-template?branch=master)
[![GitHub License](https://img.shields.io/github/license/kawofong/temporal-python-template)](https://github.com/kawofong/temporal-python-template/blob/main/LICENSE)

## Introduction

A modern, production-ready template for building Temporal applications using the [Temporal Python SDK](https://docs.temporal.io/dev-guide/python). The repository now includes a `spec_builder` workflow package plus a local GUI stack that turns local artifacts into an implementation-ready Temporal specification with structured review checkpoints and persisted outputs.

## What's Included

- Complete testing setup with async support and coverage enforcement
- Pre-configured development tooling with Ruff, Poe, and pre-commit
- Documentation for Temporal patterns and testing
- [AGENTS.md](https://agents.md/) guidance for coding agents working in the repo
- A `src/workflows/spec_builder/` workflow package with typed models, child workflows, model-backed activities, and tests
- A `src/spec_builder_gui/` local web app backend and React frontend for job submission, review, and revision flows

## Getting Started

### Prerequisites

- [uv](https://docs.astral.sh/uv/)
- [Temporal CLI](https://docs.temporal.io/cli#install)
- `GEMINI_API_KEY` or `GOOGLE_API_KEY` for the default Gemini models, or the key of whichever provider a stage's model names

### Local Environment

`ModelExecutor` loads the nearest project `.env` automatically via `python-dotenv`, without overriding variables that are already exported in the shell.

Example:

```bash
GEMINI_API_KEY=your-key-here
SPEC_BUILDER_MODEL_REQUEST_TIMEOUT_SECONDS=60
SPEC_BUILDER_MODEL_MAX_ATTEMPTS=3
```

### Quick Start

1. **Clone and set up the project**

   ```bash
   git clone https://github.com/kawofong/temporal-python-template.git
   cd temporal-python-template
   uv sync --dev
   npm ci   # only for architecture mode; see below
   ```

1. **Install development hooks**

   ```bash
   uv run poe pre-commit-install
   ```

1. **Start Temporal**

   ```bash
   temporal server start-dev
   ```

1. **Run the spec builder worker**

   ```bash
   export GEMINI_API_KEY=your-api-key
   uv run -m src.workflows.spec_builder.worker
   ```

1. **Build the local GUI frontend**

   ```bash
   cd src/spec_builder_gui/frontend
   npm install
   npm run build
   cd ../../.   cd ../../...
   ```

1. **Run the local GUI backend**

   ```bash
   uv run -m src.spec_builder_gui.server
   ```

1. **Open the GUI**

   Visit `http://127.0.0.1:8080`. The worker and the GUI backend are separate processes, so both must be running.

1. **Execute the sample spec builder workflow directly**

   ```bash
   uv run -m src.workflows.spec_builder.spec_builder_workflow
   ```

1. **Run the tests**

   ```bash
   uv run poe test
   ```

## Spec Builder Workflow

The workflow package lives in [`src/workflows/spec_builder/`](./src/workflows/spec_builder/) and is the backend contract for the local GUI in [`src/spec_builder_gui/`](./src/spec_builder_gui/). The workflow exposes stable queries and signals so the GUI can submit jobs, poll status, preview drafts, answer clarification requests, approve or reject results, cancel runs, and launch revision jobs from prior output.

### What It Does

- Accepts a typed `SpecBuilderInput` with local artifact references, user intent, output targets, and an output directory
- Normalizes local markdown, text, image, diagram, slide, and PDF-like inputs into a persisted acquisition bundle
- Produces structured extraction and intent classification outputs before drafting
- Reviews the draft against Temporal best practices using a local best-practices corpus
- Supports bounded revision loops, clarification pauses, human approval gates, and cancellation
- Materializes final outputs into both `.spec_builder_runtime/<job_id>/` and the requested local `output_directory`

### Fixed Stage Pipeline

The parent workflow coordinates seven fixed stages in this order:

1. `acquire_content` activity
1. `run_structured_extraction` activity
1. `run_intent_classification` activity
1. `run_extraction_critic` activity
1. `run_draft_spec` activity
1. `TemporalBestPracticesReviewWorkflow` child workflow
1. `run_supervisor_decision` activity

The supervisor can:

- accept the draft
- request a draft-only revision
- request a restart from acquisition/extraction
- ask for human clarification

When `requireHumanApproval` is enabled, a supervisor `accept` moves the workflow into a human approval wait state until `approve_draft` or `reject_draft` is received. The GUI can also send `force_finalize_draft` to finalize the current reviewed draft with an explicit human override note, even when high-severity best-practices violations are still present.

### GUI-Ready Workflow API

Queries:

- `get_status`: phase, iteration, restart counters, pending human action, open questions, and warnings
- `get_live_state`: one atomic snapshot of the status, current draft, best-practices violations, and traceability summary
- `get_traceability_summary`: traceability entries and uncovered claims for the current draft, or `None`

Signals:

- `submit_clarification`
- `approve_draft`
- `force_finalize_draft`
- `reject_draft`
- `cancel_job`

These handlers are the live integration surface used by the same-machine GUI status panel and review surface.

## Spec Builder GUI

The GUI backend and frontend live under [`src/spec_builder_gui/`](./src/spec_builder_gui/). The browser talks only to the local `aiohttp` backend, which persists GUI job metadata under `.spec_builder_runtime/gui/` and proxies Temporal workflow operations.

### Backend API

- `POST /api/artifacts`: upload one PDF or image artifact into local runtime storage
- `POST /api/jobs`: create and start a `SpecBuilderWorkflow`
- `GET /api/jobs`: list and refresh jobs
- `GET /api/jobs/:jobId`: load job detail, current draft markdown, stage state, violations, and review actions
- `POST /api/jobs/:jobId/review`: approve, reject, force-finalize, clarify, or cancel an active job
- `POST /api/jobs/:jobId/revise`: create a linked revision job from prior output plus revision notes
- `GET /api/jobs/:jobId/download`: download final markdown output
- `POST /api/system/pick-output-directory`: attempt a native local directory picker on supported hosts

### Frontend Views

- `New Job`: upload artifacts, set the output directory, and launch a workflow
- `Jobs`: browse active and historical jobs with search and status filters
- `Job Detail`: inspect live phase/timeline state, markdown drafts, violations, and review controls
- `Past Specs`: browse completed, failed, and cancelled jobs and launch revisions

### Frontend Development

```bash
cd src/spec_builder_gui/frontend
npm install
npm test
npm run build
```

### Runtime Outputs

The workflow persists large artifacts under `.spec_builder_runtime/<job_id>/` using stage-specific folders such as:

- `raw`
- `normalized`
- `acquisition`
- `extraction`
- `drafts`
- `review`
- `clarifications`
- `corpus`
- `final`

The final persistence step writes real local files into the requested `output_directory`, including:

- `spec.md`
- `spec.json`
- `traceability.json`
- `best_practices_review.json`
- `issues.json`

### Model Configuration

Runtime configuration is handled by [`src/workflows/spec_builder/model_executor.py`](./src/workflows/spec_builder/model_executor.py). Every model-backed stage reads its model from one `provider:model` setting; the default for each is Gemini.

Required for the defaults:

- `GEMINI_API_KEY` or `GOOGLE_API_KEY`

Optional overrides:

- `SPEC_BUILDER_<STAGE>_MODEL`, for `ACQUIRE_CONTENT`, `STRUCTURED_EXTRACTION`, `INTENT_CLASSIFICATION`, `EXTRACTION_CRITIC`, `DRAFT_SPEC`, `BEST_PRACTICES_REVIEW`, `SUPERVISOR_DECISION` and `DESIGN` (default `google:gemini-3.1-pro-preview`), for example `anthropic:claude-sonnet-4-5` with that provider's API key
- `SPEC_BUILDER_EMBEDDING_MODEL` (default `google:gemini-embedding-001`; only `google` is supported)
- `SPEC_BUILDER_MODEL_REQUEST_TIMEOUT_SECONDS` (default `60`)
- `SPEC_BUILDER_MODEL_MAX_ATTEMPTS` (default `3`)

A value without `provider:` fails naming its setting. The retired `SPEC_BUILDER_GEMINI_*` variables fail at startup naming the setting that replaces each.

### Agent Runtime

Stages run agents on [Pydantic AI](https://pydantic.dev/docs/ai/) through [`step_agent.py`](./src/workflows/spec_builder/step_agent.py). One environment variable picks how a job's agents run:

- `SPEC_BUILDER_DURABLE_AGENTS` (default `true`): each model call and tool call is its own activity, so a retry resumes mid-run. Set it to `false` (or `0`, `no`) and the whole agent run is one activity, so a retry repeats the run.

The GUI reads the variable when it starts a job and records it in the job's `durable_agents` option, so changing it never alters a job already running.

An MCP tool marked `approval_required` holds its stage: the job detail page lists the pending calls, and one decision for every call resumes the stage. An unanswered hold ends the job as `needs_clarification` 24 hours after it began, even across continue-as-new.

### Architecture Mode

With `SPEC_BUILDER_ARCHITECTURE_MODE=true` (and `twf` on `PATH`), a job can ask for a Temporal design. After the spec is accepted, a design agent writes `.twf` following [temporal-architect](https://www.npmjs.com/package/@temporal-architect/claude-plugin)'s design skill, checks it with the `twf` MCP tools, and is sent the parser's errors to repair it until `twf parse` reports none or `max_twf_revisions` (default `2`) repairs are spent. A design still invalid then adds a job warning; the markdown spec completes either way. Its model is `SPEC_BUILDER_DESIGN_MODEL`.

Setup, besides `uv sync --dev --extra architecture`: `npm ci` at the repository root, which installs the skills. The worker checks at startup, when architecture mode is on, that `twf --version` equals the skills' `MANIFEST.json` version and that every skill file matches its hash. Move `twf-cli` in `pyproject.toml` and `@temporal-architect/claude-plugin` in `package.json` together.

### Package Layout

- [`src/workflows/spec_builder/custom_types.py`](./src/workflows/spec_builder/custom_types.py): typed Pydantic contracts for workflow state, signals, queries, and stage outputs
- [`src/workflows/spec_builder/spec_builder_workflow.py`](./src/workflows/spec_builder/spec_builder_workflow.py): parent workflow orchestration, queries, signals, approval gates, restart rules, and sample local execution
- [`src/workflows/spec_builder/spec_builder_activities.py`](./src/workflows/spec_builder/spec_builder_activities.py): activity boundaries for acquisition, model-backed stages, retrieval, and final artifact persistence
- [`src/workflows/spec_builder/prompts/activity_prompts.py`](./src/workflows/spec_builder/prompts/activity_prompts.py): prompt builders for each model-backed stage
- [`src/workflows/spec_builder/model_executor.py`](./src/workflows/spec_builder/model_executor.py): model adapter with per-stage `provider:model` settings, retries, typed parsing, and attachments
- [`src/workflows/spec_builder/worker.py`](./src/workflows/spec_builder/worker.py): worker bootstrap for the `spec-builder-task-queue`
- [`src/workflows/spec_builder/tests/`](./src/workflows/spec_builder/tests/): workflow, activity, worker, and executor tests
- [`src/spec_builder_gui/server.py`](./src/spec_builder_gui/server.py): local `aiohttp` backend that stores GUI metadata and proxies Temporal operations
- [`src/spec_builder_gui/frontend/`](./src/spec_builder_gui/frontend/): React + TypeScript frontend for the local GUI
- [`src/spec_builder_gui/server_tests.py`](./src/spec_builder_gui/server_tests.py): backend API/service tests
- [`src/resources/sa_workflow_design_best_practices_critic_guide.md`](./src/resources/sa_workflow_design_best_practices_critic_guide.md): default Temporal best-practices review corpus

## Development

- Run tests: `uv run poe test`
- Run lint: `uv run poe lint`
- Run formatting: `uv run poe format`
- Start the worker: `uv run -m src.workflows.spec_builder.worker`
- Start the GUI backend: `uv run -m src.spec_builder_gui.server`
- Run frontend tests: `cd src/spec_builder_gui/frontend && npm test`
- Build frontend assets: `cd src/spec_builder_gui/frontend && npm run build`
- Execute the sample workflow: `uv run -m src.workflows.spec_builder.spec_builder_workflow`

## Next Steps

- See [`docs/temporal-patterns.md`](./docs/temporal-patterns.md) for advanced Temporal patterns
- See [`docs/testing.md`](./docs/testing.md) for Temporal testing guidance
- See [`DEVELOPERS.md`](./DEVELOPERS.md) for local development tips and workflows

## License

[MIT License](LICENSE).
