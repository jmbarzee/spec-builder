// @vitest-environment node
import { fileURLToPath } from "node:url";

import { build } from "vite";
import type { OutputAsset, OutputChunk, RollupOutput } from "rollup";
import { describe, expect, test } from "vitest";

const VISUALIZER = "@temporal-architect/visualizer";
// A rule that selects the page itself, as opposed to something inside `.twf-scope`.
const PAGE_RULE = /(^|[}{,])\s*(html|body|\*|:root|#root)(?![\w-])/;

describe("design view build", () => {
  test("should emit the visualizer as a lazy chunk with scoped styles", async () => {
    const result = (await build({
      // The .ts file, since a stale compiled vite.config.js would otherwise win.
      configFile: fileURLToPath(new URL("../../vite.config.ts", import.meta.url)),
      logLevel: "silent",
      build: { write: false },
    })) as RollupOutput;
    const chunks = result.output.filter((file): file is OutputChunk => file.type === "chunk");
    const assets = new Map(
      result.output
        .filter((file): file is OutputAsset => file.type === "asset")
        .map((file) => [file.fileName, String(file.source)]),
    );
    const entry = chunks.find((chunk) => chunk.isEntry)!;
    const lazy = chunks.find((chunk) => Object.keys(chunk.modules).some((id) => id.includes(VISUALIZER)))!;

    expect(lazy).not.toBe(entry);
    expect(lazy.isDynamicEntry).toBe(true);
    expect(entry.imports).not.toContain(lazy.fileName);
    expect(Object.keys(entry.modules).some((id) => id.includes(VISUALIZER))).toBe(false);
    expect(assets.get("index.html")).not.toContain(lazy.fileName);

    const lazyCss = [...(lazy.viteMetadata?.importedCss ?? [])].map((name) => assets.get(name)!);
    expect(lazyCss.length).toBeGreaterThan(0);
    for (const css of lazyCss) {
      expect(css).toContain(".twf-scope");
      expect(css).not.toMatch(PAGE_RULE);
    }
    for (const name of entry.viteMetadata?.importedCss ?? []) {
      expect(assets.get(name)).not.toContain(".view-shell");
    }
  }, 120_000);
});
