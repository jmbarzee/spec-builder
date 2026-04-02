import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { NewJobPage } from "../pages/new-job-page";

function jsonResponse(payload: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response;
}

describe("NewJobPage", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  test("should validate required fields before job submission", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter
        initialEntries={["/"]}
        future={{
          v7_relativeSplatPath: true,
          v7_startTransition: true,
        }}
      >
        <Routes>
          <Route path="/" element={<NewJobPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: /launch spec workflow/i }));

    expect(screen.getByText(/project name is required/i)).toBeInTheDocument();
  });

  test("should upload files in multiple rounds and allow removing one before submit", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        artifactId: "artifact-1",
        filename: "brief.pdf",
        mimeType: "application/pdf",
        sizeBytes: 1000,
        kind: "pdf",
      }),
    );
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        artifactId: "artifact-2",
        filename: "diagram.png",
        mimeType: "image/png",
        sizeBytes: 2000,
        kind: "image",
      }),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter
        initialEntries={["/"]}
        future={{
          v7_relativeSplatPath: true,
          v7_startTransition: true,
        }}
      >
        <Routes>
          <Route path="/" element={<NewJobPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const fileInput = screen.getByLabelText(/artifacts/i);
    await user.upload(fileInput, new File(["pdf"], "brief.pdf", { type: "application/pdf" }));
    await user.upload(fileInput, new File(["png"], "diagram.png", { type: "image/png" }));

    await waitFor(() => {
      expect(screen.getByText("brief.pdf")).toBeInTheDocument();
      expect(screen.getByText("diagram.png")).toBeInTheDocument();
    });

    const removeButtons = screen.getAllByRole("button", { name: /remove/i });
    await user.click(removeButtons[0]);

    expect(screen.queryByText("brief.pdf")).not.toBeInTheDocument();
    expect(screen.getByText("diagram.png")).toBeInTheDocument();
  });
});
