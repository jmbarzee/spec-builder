import { lazy, Suspense, useEffect, useState } from "react";

import { api } from "../api";
import type { TwfDesign } from "../types";

const TwfVisualizer = lazy(() => import("./twf-visualizer"));

type View = "visualizer" | "source";

/** The job's `.twf` design. `designUri` changes when a new design is recorded. */
export function DesignPanel({ jobId, designUri }: { jobId: string; designUri?: string }) {
  const [design, setDesign] = useState<TwfDesign | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>("visualizer");

  useEffect(() => {
    let cancelled = false;
    api
      .getTwfDesign(jobId)
      .then((next) => {
        if (!cancelled) {
          setDesign(next);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setDesign(null);
          setError(caught instanceof Error ? caught.message : "Design load failed.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, designUri]);

  return (
    <section className="surface-card">
      <div className="section-heading">
        <h2>Design</h2>
        <p>The system design as temporal-architect parsed it.</p>
      </div>
      {design ? (
        <>
          <div className="inline-actions">
            {(["visualizer", "source"] as const).map((name) => (
              <button
                key={name}
                type="button"
                className={view === name ? "primary-button" : "ghost-button"}
                aria-pressed={view === name}
                onClick={() => setView(name)}
              >
                {name === "visualizer" ? "Visualizer" : "Source"}
              </button>
            ))}
          </div>
          {view === "source" ? (
            <pre className="design-source">{design.source}</pre>
          ) : (
            <Suspense fallback={<p className="muted-copy">Loading visualizer...</p>}>
              <TwfVisualizer design={design} />
            </Suspense>
          )}
        </>
      ) : (
        <p className="muted-copy">{error ?? "Loading design..."}</p>
      )}
    </section>
  );
}
