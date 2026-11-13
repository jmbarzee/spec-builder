import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { WorkflowTimeline } from "../components/workflow-timeline";

describe("WorkflowTimeline", () => {
  test("should label the design stage and show its status", () => {
    render(
      <WorkflowTimeline
        stages={[
          { stage: "supervisor", status: "completed" },
          { stage: "design", status: "running" },
        ]}
      />,
    );

    expect(screen.getByText("System Design")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
  });
});
