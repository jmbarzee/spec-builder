import {
  Visualizer,
  mountNodeTypeStyles,
  type ParserGraph,
  type TWFFile,
} from "@temporal-architect/visualizer";
import "@temporal-architect/visualizer/styles.css";

import type { TwfDesign } from "../types";

mountNodeTypeStyles();

/** The design in the visualizer. This module is the lazy chunk; nothing else imports it. */
export default function TwfVisualizer({ design }: { design: TwfDesign }) {
  return (
    <div className="twf-scope design-visualizer" data-theme="dark">
      <Visualizer
        ast={design.parse as unknown as TWFFile}
        parserGraph={(design.graph ?? undefined) as ParserGraph | undefined}
      />
    </div>
  );
}
