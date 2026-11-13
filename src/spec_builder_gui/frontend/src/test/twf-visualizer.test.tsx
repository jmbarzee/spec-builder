import { render } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import TwfVisualizer from "../components/twf-visualizer";

vi.mock("@temporal-architect/visualizer", () => ({
  Visualizer: () => <div>canvas</div>,
  mountNodeTypeStyles: () => {},
}));
vi.mock("@temporal-architect/visualizer/styles.css", () => ({}));

test("should size the panel on an element other than the one carrying the scoped page rules", () => {
  // The scoped stylesheet sets `.twf-scope { height: 100% }`, which would collapse the panel's
  // own height if both classes sat on one element.
  const { container } = render(
    <TwfVisualizer design={{ source: "", parse: {}, graph: {} }} />,
  );

  const scope = container.querySelector(".twf-scope");
  expect(scope?.parentElement).toHaveClass("design-visualizer");
  expect(scope).not.toHaveClass("design-visualizer");
  expect(scope).toHaveAttribute("data-theme", "dark");
});
