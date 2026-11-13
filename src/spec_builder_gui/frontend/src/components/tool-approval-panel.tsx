import { useState } from "react";

import type { DecideToolCallsRequest, PendingToolCall } from "../types";

type ToolApprovalPanelProps = {
  calls: PendingToolCall[];
  onSubmit: (payload: DecideToolCallsRequest) => Promise<void>;
};

export function ToolApprovalPanel({ calls, onSubmit }: ToolApprovalPanelProps) {
  const [decisions, setDecisions] = useState<Record<string, boolean>>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const allDecided = calls.every((call) => call.toolCallId in decisions);

  async function submit() {
    setPending(true);
    setError(null);
    try {
      await onSubmit({
        decisions: Object.fromEntries(calls.map((call) => [call.toolCallId, decisions[call.toolCallId]])),
      });
      setDecisions({});
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "Tool decision failed.");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="surface-card">
      <div className="section-heading">
        <h2>Tool Calls Awaiting Approval</h2>
        <p>The agent is paused until every call below is approved or denied.</p>
      </div>
      <ul className="copy-list">
        {calls.map((call) => (
          <li key={call.toolCallId}>
            <strong>{call.toolName}</strong> <code>{call.args}</code>
            <div className="inline-actions">
              <label>
                <input
                  type="radio"
                  name={call.toolCallId}
                  checked={decisions[call.toolCallId] === true}
                  onChange={() => setDecisions({ ...decisions, [call.toolCallId]: true })}
                />{" "}
                Approve {call.toolName}
              </label>
              <label>
                <input
                  type="radio"
                  name={call.toolCallId}
                  checked={decisions[call.toolCallId] === false}
                  onChange={() => setDecisions({ ...decisions, [call.toolCallId]: false })}
                />{" "}
                Deny {call.toolName}
              </label>
            </div>
          </li>
        ))}
      </ul>
      {error ? <p className="inline-error">{error}</p> : null}
      <button
        type="button"
        className="primary-button"
        disabled={!allDecided || pending}
        onClick={() => void submit()}
      >
        {pending ? "Sending..." : "Send Decisions"}
      </button>
    </section>
  );
}
