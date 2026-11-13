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

type UploadPayload = { artifactId: string; filename: string; mimeType: string; kind: string };

/** Route fetch by URL: features, then uploads in order; returns the mock for call inspection. */
function mockApi(features: { architectureMode: boolean }, uploads: UploadPayload[] = []) {
  const pending = [...uploads];
  return vi.mocked(fetch).mockImplementation(async (input) => {
    if (input === "/api/system/features") {
      return jsonResponse(features);
    }
    if (input === "/api/artifacts") {
      return jsonResponse({ ...pending.shift(), sizeBytes: 1000 });
    }
    return jsonResponse({ jobId: "job-1", workflowId: "wf-1", status: "queued", createdAt: "" }, 201);
  });
}

function renderPage() {
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
        <Route path="/jobs/:jobId" element={<p>job page</p>} />
      </Routes>
    </MemoryRouter>,
  );
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
    renderPage();

    await user.click(screen.getByRole("button", { name: /launch spec workflow/i }));

    expect(screen.getByText(/project name is required/i)).toBeInTheDocument();
  });

  test("should upload files in multiple rounds and allow removing one before submit", async () => {
    const uploads = [
      { artifactId: "artifact-1", filename: "brief.pdf", mimeType: "application/pdf", kind: "pdf" },
      { artifactId: "artifact-2", filename: "diagram.png", mimeType: "image/png", kind: "image" },
    ];
    mockApi({ architectureMode: false }, uploads);

    const user = userEvent.setup();
    renderPage();

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

  test("should hide the architecture-mode checkbox when the feature is off", async () => {
    const fetchMock = mockApi({ architectureMode: false });
    renderPage();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/system/features", undefined));

    expect(screen.queryByLabelText(/architecture mode/i)).not.toBeInTheDocument();
  });

  test("should show the checkbox when the feature is on and submit the chosen mode", async () => {
    const upload = { artifactId: "artifact-1", filename: "brief.pdf", mimeType: "application/pdf", kind: "pdf" };
    const fetchMock = mockApi({ architectureMode: true }, [upload]);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByLabelText(/architecture mode/i));
    await user.type(screen.getByLabelText(/project name/i), "Sync");
    await user.type(screen.getByLabelText(/output directory/i), "/tmp/out");
    await user.upload(
      screen.getByLabelText(/artifacts/i),
      new File(["pdf"], "brief.pdf", { type: "application/pdf" }),
    );
    await screen.findByText("uploaded");
    await user.click(screen.getByRole("button", { name: /launch spec workflow/i }));

    await screen.findByText("job page");
    const jobCall = fetchMock.mock.calls.find(([url]) => url === "/api/jobs");
    expect(JSON.parse(String(jobCall?.[1]?.body))).toMatchObject({ architectureMode: true });
  });
});
