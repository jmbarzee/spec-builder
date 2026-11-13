import type { StageState } from "../types";

const STAGE_LABELS: Record<StageState["stage"], string> = {
  acquire: "Acquire Content",
  extract: "Structured Extraction",
  classify: "Intent Classification",
  critic: "Extraction Critic",
  draft: "Draft Spec",
  best_practices_review: "Best-Practices Review",
  supervisor: "Supervisor Decision",
  design: "System Design",
};

export function WorkflowTimeline({ stages }: { stages: StageState[] }) {
  return (
    <ol className="timeline-list">
      {stages.map((stage) => (
        <li key={stage.stage} className={`timeline-item is-${stage.status.replace(/_/g, "-")}`}>
          <div className="timeline-dot" aria-hidden="true" />
          <div className="timeline-copy">
            <strong>{STAGE_LABELS[stage.stage]}</strong>
            <span className="timeline-status">{stage.status.replace(/_/g, " ")}</span>
            {stage.message ? <p>{stage.message}</p> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
