import type { ToolActivityItem } from "../types";

const STATUS_LABELS = {
  running: "RUNNING",
  succeeded: "DONE",
  failed: "FAILED",
  awaiting_approval: "APPROVAL REQUIRED",
} as const;

export function ToolActivity({ item }: { item: ToolActivityItem }) {
  return (
    <article
      className={`tool-activity tool-activity-${item.status}`}
      aria-label={`${item.action} ${STATUS_LABELS[item.status]}`}
    >
      <div className="tool-activity-line">
        <span className="tool-kind">TOOL</span>
        <strong>{item.action}</strong>
        {item.target && <span className="tool-target">· {item.target}</span>}
        {item.detail && <span className="tool-detail">· {item.detail}</span>}
        <span className="tool-status">
          <span className="tool-status-dot" aria-hidden="true" />
          {STATUS_LABELS[item.status]}
        </span>
      </div>
      {item.status === "failed" && item.error && (
        <p className="tool-activity-error">{shorten(item.error)}</p>
      )}
    </article>
  );
}

function shorten(value: string): string {
  return value.length > 220 ? `${value.slice(0, 217)}…` : value;
}
