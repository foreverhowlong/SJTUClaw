import type { ToolActivityItem } from "../types";

const STATUS_LABELS = {
  running: "RUNNING",
  succeeded: "DONE",
  failed: "FAILED",
  awaiting_approval: "REVIEW",
} as const;

export function ToolActivity({
  item,
  onResolveApproval,
}: {
  item: ToolActivityItem;
  onResolveApproval?: (approvalId: string, approved: boolean, reason: string) => Promise<void>;
}) {
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
      {item.approval && item.status === "awaiting_approval" && onResolveApproval && (
        <ApprovalControls item={item} onResolve={onResolveApproval} />
      )}
      {item.download && item.status === "succeeded" && (
        <a className="download-button" href={item.download.downloadUrl}>
          DOWNLOAD {item.download.filename}
        </a>
      )}
    </article>
  );
}

function ApprovalControls({
  item,
  onResolve,
}: {
  item: ToolActivityItem;
  onResolve: (approvalId: string, approved: boolean, reason: string) => Promise<void>;
}) {
  const approval = item.approval!;
  const reject = () => {
    const reason = window.prompt("拒绝原因（可选）", "") ?? "";
    void onResolve(approval.approvalId, false, reason);
  };
  return (
    <div className="approval-review">
      <div className="approval-scope" title={approval.workspace ?? "Workspace not set"}>
        <span className="approval-scope-label">SCOPE</span>
        <span className="approval-workspace">
          {approval.workspace ?? "Workspace not set"}
        </span>
      </div>
      <div className="approval-actions">
        <button
          className="approval-approve"
          type="button"
          onClick={() => void onResolve(approval.approvalId, true, "")}
        >
          Approve
        </button>
        <button className="approval-deny" type="button" onClick={reject}>
          Deny
        </button>
      </div>
      <details className="approval-details">
        <summary>Detail</summary>
        <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>
      </details>
    </div>
  );
}

function shorten(value: string): string {
  return value.length > 220 ? `${value.slice(0, 217)}…` : value;
}
