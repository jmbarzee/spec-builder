# Demo Builder GUI Spec

## Purpose

Provide a local web GUI for submitting demo-builder source files, choosing a local directory for saved outputs, monitoring workflow progress, and handling review checkpoints without dropping to the CLI.

## Goals

1. Let a user upload one or more source files, including images.
2. Let a user choose or enter a local output directory for generated artifacts.
3. Start the `DemoBuilderWorkflow` from the GUI.
4. Show workflow stage, status, open questions, and current draft progress.
5. Surface pending review checkpoints and allow the user to approve or request revision.
6. Save generated artifacts to the chosen local directory and expose links to the saved files.

## Non-Goals

1. Multi-user authentication.
2. Remote/cloud deployment.
3. Arbitrary filesystem browsing in the browser without local host assistance.
4. Replacing the existing Temporal worker CLI entrypoints.

## Primary User Story

As a local developer or product reviewer, I want to upload images and supporting docs, choose where the final spec should be written on my machine, run the workflow from a browser, and approve checkpoints as the workflow progresses so I can get a saved `spec.md` and related artifacts without manually building Temporal requests.

## Functional Requirements

### Submission

The GUI shall provide:

1. A multi-file upload field for images, markdown, text, and PDFs.
2. A `created_by` field.
3. A local output directory field.
4. A button to launch a native directory picker when available on the local host.
5. A submit action that starts the workflow only after at least one file and an output directory are provided.

### Workflow Monitoring

The GUI shall show:

1. Current workflow status.
2. Current workflow stage.
3. Number of processed documents.
4. Open questions discovered during analysis.
5. Draft metadata and markdown preview when available.

### Review Flow

The GUI shall support:

1. Showing the active review checkpoint ID and summary.
2. Approving a checkpoint.
3. Sending reviewer feedback with `comment`, `revise`, or `reject` actions.
4. Refreshing automatically while the workflow is running or awaiting review.

### Saved Outputs

The system shall write generated artifacts to the chosen local directory.

The saved outputs shall include:

1. `spec.md`
2. `spec.json`
3. `traceability.json`
4. `issues.json`
5. `demo_manifest.json`
6. `quickstart.md`
7. review/publication metadata files
8. optional demo bundle when available

The GUI shall display links or paths to the saved files after completion.

## UX Requirements

1. The page should work on desktop and mobile.
2. The upload form, workflow status, draft preview, and saved outputs should be visible without navigation to separate pages.
3. Pending review actions should be visually prominent.
4. Errors should be shown inline with actionable text.
5. The page should feel intentionally designed rather than default boilerplate.

## Technical Requirements

1. Use a local `aiohttp` web app so no extra frontend build system is required.
2. Keep file writes in activities or backend-side service boundaries, not in workflow code.
3. Reuse the existing `DemoBuilderWorkflow` task queue and typed models.
4. The web app should be able to start workflows, query status, and send review signals through Temporal.
5. Uploaded files should be copied into a local working directory for the submission.
6. Generated outputs should be materialized on disk so the selected output directory contains real files, not only placeholder URIs.

## Suggested Layout

The GUI should present four primary panels:

1. Submission form
2. Workflow status and stage timeline
3. Review checkpoint and actions
4. Draft preview and saved outputs

## Acceptance Criteria

1. A user can upload one or more image files from the GUI.
2. A user can enter or select a local output directory.
3. Submitting the form starts a Temporal workflow execution.
4. The GUI updates while the workflow is running.
5. If the workflow pauses for review, the GUI shows the checkpoint and allows approval.
6. When the workflow completes, `spec.md` is present in the chosen output directory.
7. The GUI shows the saved artifact paths after completion.
