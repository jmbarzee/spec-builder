import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { JobDetailPage } from "../pages/job-detail-page";
import type { JobDetail } from "../types";

function jsonResponse(payload: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  } as Response;
}

function buildDetail(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    jobId: "job-1",
    workflowId: "workflow-1",
    projectName: "Studio Spec",
    status: "waiting_for_human",
    currentPhase: "waiting_for_approval",
    iteration: 1,
    createdAt: "2026-04-02T10:00:00Z",
    updatedAt: "2026-04-02T10:02:00Z",
    outputDirectory: "/tmp/out",
    artifacts: [],
    stages: [
      { stage: "acquire", status: "completed" },
      { stage: "extract", status: "completed" },
      { stage: "classify", status: "completed" },
      { stage: "critic", status: "completed" },
      { stage: "draft", status: "completed" },
      { stage: "best_practices_review", status: "completed" },
      { stage: "supervisor", status: "completed" },
    ],
    openQuestions: [],
    warnings: [],
    currentDraftMarkdown: "# Spec\n\nReview me.\n",
    finalSpecMarkdown: null,
    reviewFeedbackHistory: [],
    bestPracticesViolations: [],
    currentDraftMetadata: {
      draftVersion: 2,
      summary: "Second draft",
      markdownSpecUri: "file:///tmp/spec.md",
      structuredSpecUri: "file:///tmp/spec.json",
      openQuestionsCount: 0,
      assumptionCount: 1,
    },
    traceabilitySummary: {
      entries: [],
      uncoveredClaims: [],
    },
    pendingHumanAction: "approval",
    finalWorkflowStatus: null,
    artifactUris: {},
    canApprove: true,
    canReject: true,
    canProvideFeedback: false,
    canDownload: false,
    canCreateRevision: false,
    canApproveRevisionAsFinal: false,
    lastSyncedAt: "2026-04-02T10:02:00Z",
    ...overrides,
  };
}

describe("JobDetailPage", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  test("should render waiting approval controls and send approve review requests", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock
      .mockResolvedValueOnce(jsonResponse(buildDetail()))
      .mockResolvedValueOnce(jsonResponse(buildDetail()));

    const user = userEvent.setup();
    render(
      <MemoryRouter
        initialEntries={["/jobs/job-1"]}
        future={{
          v7_relativeSplatPath: true,
          v7_startTransition: true,
        }}
      >
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Studio Spec")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/jobs/job-1/review",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(screen.getByText("Acquire Content")).toBeInTheDocument();
  });

  test("should show the revision launcher for a completed job", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(
        buildDetail({
          status: "completed",
          currentPhase: "completed",
          pendingHumanAction: null,
          finalSpecMarkdown: "# Final\n\nReady.\n",
          canApprove: false,
          canReject: false,
          canCreateRevision: true,
          canApproveRevisionAsFinal: false,
          canDownload: true,
        }),
      ),
    );

    render(
      <MemoryRouter
        initialEntries={["/jobs/job-1"]}
        future={{
          v7_relativeSplatPath: true,
          v7_startTransition: true,
        }}
      >
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /create revision/i })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /download final markdown/i })).toHaveAttribute(
        "href",
        "/api/jobs/job-1/download",
      );
    });
  });

  test("should allow approving a closed revision into the output folder", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(
          buildDetail({
            status: "completed",
            currentPhase: "completed",
            pendingHumanAction: null,
            parentJobId: "job-parent",
            finalSpecMarkdown: "# Final Revision\n\nReady.\n",
            canApprove: false,
            canReject: false,
            canCreateRevision: true,
            canApproveRevisionAsFinal: true,
            canDownload: false,
          }),
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          buildDetail({
            status: "completed",
            currentPhase: "completed",
            pendingHumanAction: null,
            parentJobId: "job-parent",
            finalSpecMarkdown: "# Final Revision\n\nReady.\n",
            canApprove: false,
            canReject: false,
            canCreateRevision: true,
            canApproveRevisionAsFinal: false,
            canDownload: true,
          }),
        ),
      );

    const user = userEvent.setup();
    render(
      <MemoryRouter
        initialEntries={["/jobs/job-1"]}
        future={{
          v7_relativeSplatPath: true,
          v7_startTransition: true,
        }}
      >
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /approve revision as final/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /approve revision as final/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/jobs/job-1/approve-revision",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });
});
