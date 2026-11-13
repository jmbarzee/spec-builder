import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The visualizer's stylesheets style the page itself (`html`, `body`, `#root`, `:root`, `*`).
// Moving those rules onto `.twf-scope` confines them to the design panel's container.
const SCOPE = ".twf-scope";
const SCOPED_PACKAGES = /node_modules\/(@temporal-architect\/visualizer|@jmbarzee\/force-graph)\//;
const PAGE_SELECTOR = /^(html|body|:root|#root)(?![\w-])/;
const THEME_SELECTOR = /^(\[data-theme|\.vscode-)/;

function scopeSelector(selector: string): string[] {
  const trimmed = selector.trim();
  if (PAGE_SELECTOR.test(trimmed)) {
    return [trimmed.replace(PAGE_SELECTOR, SCOPE)];
  }
  if (THEME_SELECTOR.test(trimmed)) {
    // The scope element itself carries the theme attribute, so match it and its descendants.
    return [`${SCOPE}${trimmed}`, `${SCOPE} ${trimmed}`];
  }
  return [`${SCOPE} ${trimmed}`];
}

const scopeVisualizerCss = {
  postcssPlugin: "scope-visualizer-css",
  Once(root: any, { result }: any) {
    if (!SCOPED_PACKAGES.test(result.opts.from ?? "")) {
      return;
    }
    root.walkRules((rule: any) => {
      if (/keyframes$/.test(rule.parent?.name ?? "")) {
        return;
      }
      rule.selectors = rule.selectors.flatMap(scopeSelector);
    });
  },
};

export default defineConfig({
  plugins: [react()],
  css: { postcss: { plugins: [scopeVisualizerCss] } },
  server: {
    port: 5173,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
