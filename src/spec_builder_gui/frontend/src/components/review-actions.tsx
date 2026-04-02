import { useState } from "react";

import type { JobDetail, SubmitReviewRequest } from "../types";

type ReviewActionsProps = {
  detail: JobDetail;
  onSubmit: (payload: SubmitReviewRequest) => Promise<void>;
};

export function ReviewActions({ detail, onSubmit }: ReviewActionsProps) {
  const [text, setText] = useState("");
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function runAction(action: SubmitReviewRequest["action"]) {
    if (action === "reject" && !text.trim()) {
      setError("Rejecting a draft requires written feedback.");
      return;
    }
    if (action === "feedback" && !text.trim()) {
      setError("Clarification feedback cannot be empty.");
      return;
    }
    setPendingAction(action);
    setError(null);
    try {
      await onSubmit({
        action,
        text: text.trim() || undefined,
      });
      if (action !== "approve") {
        setText("");
      }
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "Review action failed.");
    } finally {
      setPendingAction(null);
    }
  }

  const showTextArea = detail.canReject || detail.canProvideFeedback || detail.status === "running";

  return (
    <section className="surface-card review-card">
      <div className="section-heading">
        <h2>Review & Intervention</h2>
        <p>
          {detail.pendingHumanAction === "approval"
            ? "A human approval gate is active."
            : detail.pendingHumanAction === "clarification"
              ? "The workflow is waiting for written clarification."
              : "You can monitor the workflow here and cancel if needed."}
        </p>
      </div>

      {showTextArea ? (
        <label className="field">
          <span>
            {detail.canProvideFeedback
              ? "Clarification"
              : detail.canReject
                ? "Rejection feedback"
                : "Notes"}
          </span>
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Explain what should change, what is ambiguous, or why this draft should be revised."
            rows={5}
          />
        </label>
      ) : null}

      {error ? <p className="inline-error">{error}</p> : null}

      <div className="review-actions">
        {detail.canApprove ? (
          <button
            type="button"
            className="primary-button"
            disabled={pendingAction !== null}
            onClick={() => void runAction("approve")}
          >
            {pendingAction === "approve" ? "Approving..." : "Approve"}
          </button>
        ) : null}
        {detail.canReject ? (
          <button
            type="button"
            className="secondary-button"
            disabled={pendingAction !== null}
            onClick={() => void runAction("reject")}
          >
            {pendingAction === "reject" ? "Rejecting..." : "Reject"}
          </button>
        ) : null}
        {detail.canProvideFeedback ? (
          <button
            type="button"
            className="ghost-button"
            disabled={pendingAction !== null}
            onClick={() => void runAction("feedback")}
          >
            {pendingAction === "feedback" ? "Sending..." : "Send Clarification"}
          </button>
        ) : null}
        {detail.status === "queued" ||
        detail.status === "running" ||
        detail.status === "waiting_for_human" ? (
          <button
            type="button"
            className="ghost-button danger-button"
            disabled={pendingAction !== null}
            onClick={() => void runAction("cancel")}
          >
            {pendingAction === "cancel" ? "Cancelling..." : "Cancel Job"}
          </button>
        ) : null}
      </div>
    </section>
  );
}
