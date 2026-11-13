import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { JobDetailPage } from "../pages/job-detail-page";
import type { JobDetail } from "../types";

vi.mock("../components/twf-visualizer", () => ({
  default: () => <div>visualizer canvas</div>,
}));

function jsonResponse(payload: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => payload } as Response;
}

function buildDetail(architectureMode: boolean): JobDetail {
  return {
    jobId: "job-1",
    workflowId: "workflow-1",
    projectName: "Studio Spec",
    status: "completed",
    currentPhase: "completed",
    iteration: 1,
    architectureMode,
    createdAt: "2026-04-02T10:00:00Z",
    updatedAt: "2026-04-02T10:02:00Z",
    outputDirectory: "/tmp/out",
    artifacts: [],
    stages: [],
    openQuestions: [],
    warnings: [],
    reviewFeedbackHistory: [],
    bestPracticesViolations: [],
    pendingHumanAction: null,
    pendingToolCalls: [],
    finalWorkflowStatus: "completed",
    artifactUris: {},
    canApprove: false,
    canReject: false,
    canProvideFeedback: false,
    canDecideToolCalls: false,
    canForceFinalize: false,
    canDownload: false,
    canCreateRevision: false,
    canApproveRevisionAsFinal: false,
  } as JobDetail;
}

function renderPage() {
  render(
    <MemoryRouter
      initialEntries={["/jobs/job-1"]}
      future={{ v7_relativeSplatPath: true, v7_startTransition: true }}
    >
      <Routes>
        <Route path="/jobs/:jobId" element={<JobDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function mockApi(architectureMode: boolean, twf: Response) {
  vi.mocked(fetch).mockImplementation(async (input) =>
    String(input).endsWith("/twf") ? twf : jsonResponse(buildDetail(architectureMode)),
  );
}

describe("design panel", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  test("should show the visualizer and the source on an architecture job", async () => {
    mockApi(
      true,
      jsonResponse({ source: "workflow Foo():\n", parse: { definitions: [] }, graph: { nodes: [] } }),
    );
    renderPage();

    expect(await screen.findByText("visualizer canvas")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Source" }));
    expect(screen.getByText(/workflow Foo\(\):/)).toBeInTheDocument();
    expect(screen.queryByText("visualizer canvas")).not.toBeInTheDocument();
  });

  test("should say so when the design is not available yet", async () => {
    mockApi(true, jsonResponse({ error: "No design is available for this job." }, 404));
    renderPage();

    expect(await screen.findByText("No design is available for this job.")).toBeInTheDocument();
  });

  test("should not appear or fetch a design on a job without architecture mode", async () => {
    mockApi(false, jsonResponse({}, 404));
    renderPage();

    await screen.findByText("Studio Spec");
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByRole("heading", { name: "Design" })).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith("/twf"))).toBe(false);
  });
});
