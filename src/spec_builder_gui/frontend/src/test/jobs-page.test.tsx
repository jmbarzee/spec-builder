import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { JobsPage } from "../pages/jobs-page";

function jsonResponse(payload: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  } as Response;
}

describe("JobsPage", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  test("should show only final jobs on the past specs page", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        items: [
          {
            jobId: "job-running",
            workflowId: "workflow-running",
            projectName: "Live Draft",
            status: "running",
            currentPhase: "draft",
            iteration: 0,
            createdAt: "2026-04-02T10:00:00Z",
            updatedAt: "2026-04-02T10:01:00Z",
          },
          {
            jobId: "job-complete",
            workflowId: "workflow-complete",
            projectName: "Released Spec",
            status: "completed",
            currentPhase: "completed",
            iteration: 1,
            createdAt: "2026-04-01T10:00:00Z",
            updatedAt: "2026-04-01T10:10:00Z",
          },
        ],
        total: 2,
      }),
    );

    render(
      <MemoryRouter
        future={{
          v7_relativeSplatPath: true,
          v7_startTransition: true,
        }}
      >
        <JobsPage finalOnly />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Released Spec")).toBeInTheDocument();
    });

    expect(screen.queryByText("Live Draft")).not.toBeInTheDocument();
  });

  test("should issue a filtered job request after search input changes", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue(
      jsonResponse({
        items: [],
        total: 0,
      }),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter
        future={{
          v7_relativeSplatPath: true,
          v7_startTransition: true,
        }}
      >
        <JobsPage />
      </MemoryRouter>,
    );

    await user.type(screen.getByLabelText(/search/i), "playlist");

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("search=playlist")),
      ).toBe(true);
    });
  });
});
