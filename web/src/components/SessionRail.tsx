import type { SessionSummary } from "../types";

interface Props {
  className?: string;
  sessions: SessionSummary[];
  activeSessionId: string | null;
  loading: boolean;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  onClose: () => void;
}

export function SessionRail({
  className = "",
  sessions,
  activeSessionId,
  loading,
  onSelect,
  onNew,
  onClose,
}: Props) {
  return (
    <aside className={`session-rail drawer-panel ${className}`}>
      <div className="panel-heading">
        <div>
          <span className="micro-label">CONVERSATIONS</span>
          <h2>Sessions</h2>
        </div>
        <button className="drawer-close" type="button" onClick={onClose} aria-label="关闭">
          ×
        </button>
      </div>
      <div className="session-list" aria-live="polite">
        {loading && <p className="muted-copy">正在载入 sessions…</p>}
        {!loading && sessions.length === 0 && (
          <div className="empty-small">
            <p>还没有 session。</p>
            <button className="underline-button" type="button" onClick={onNew}>
              创建第一个会话
            </button>
          </div>
        )}
        {sessions.map((session) => (
          <button
            type="button"
            key={session.sessionId}
            className={`session-item ${
              session.sessionId === activeSessionId ? "is-active" : ""
            }`}
            onClick={() => onSelect(session.sessionId)}
          >
            <span className="session-accent" aria-hidden="true" />
            <span className="session-title">{session.title}</span>
            <span className="session-meta">
              {session.messageCount} messages · {relativeTime(session.updatedAt)}
            </span>
          </button>
        ))}
      </div>
      <div className="rail-footer">
        <span className="micro-label">LOCAL RUNTIME</span>
        <p>Session history stays on your Gateway.</p>
      </div>
    </aside>
  );
}

function relativeTime(value: string): string {
  const timestamp = new Date(value).getTime();
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}
