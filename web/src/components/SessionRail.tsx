import { type FormEvent, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { SessionSummary } from "../types";

interface Props {
  className?: string;
  sessions: SessionSummary[];
  activeSessionId: string | null;
  loading: boolean;
  runningSessionIds: Set<string>;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  onRename: (sessionId: string, title: string) => Promise<void>;
  onDelete: (sessionId: string) => Promise<void>;
  onClose: () => void;
}

export function SessionRail({
  className = "",
  sessions,
  activeSessionId,
  loading,
  runningSessionIds,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onClose,
}: Props) {
  const [menuSessionId, setMenuSessionId] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<SessionSummary | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<SessionSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuSessionId) return;
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) {
        setMenuSessionId(null);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menuSessionId]);

  useEffect(() => {
    if (!deleteTarget) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) setDeleteTarget(null);
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [busy, deleteTarget]);

  const beginRename = (session: SessionSummary) => {
    if (runningSessionIds.has(session.sessionId)) return;
    setMenuSessionId(null);
    setRenaming(session);
    setRenameDraft(session.title);
  };

  const cancelRename = () => {
    if (busy) return;
    setRenaming(null);
    setRenameDraft("");
  };

  const submitRename = async (event: FormEvent) => {
    event.preventDefault();
    const title = renameDraft.trim();
    if (
      !renaming ||
      runningSessionIds.has(renaming.sessionId) ||
      !title ||
      title === renaming.title
    ) {
      cancelRename();
      return;
    }
    setBusy(true);
    try {
      await onRename(renaming.sessionId, title);
      setRenaming(null);
      setRenameDraft("");
    } catch {
      // App surfaces the Gateway error; keep the editor open for correction.
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget || runningSessionIds.has(deleteTarget.sessionId)) return;
    setBusy(true);
    try {
      await onDelete(deleteTarget.sessionId);
      setDeleteTarget(null);
      setMenuSessionId(null);
    } catch {
      // App surfaces the Gateway error; keep confirmation visible for retry.
    } finally {
      setBusy(false);
    }
  };

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

      <button className="new-session-button" type="button" onClick={onNew}>
        <span>NEW SESSION</span>
        <span aria-hidden="true">＋</span>
      </button>

      <div className="session-list" aria-live="polite">
        {loading && <p className="muted-copy">正在载入 sessions…</p>}
        {!loading && sessions.length === 0 && (
          <div className="empty-small"><p>还没有 session。</p></div>
        )}
        {sessions.map((session) => {
          const isActive = session.sessionId === activeSessionId;
          const isRunning = runningSessionIds.has(session.sessionId);
          if (renaming?.sessionId === session.sessionId) {
            return (
              <form
                className="session-rename-form"
                key={session.sessionId}
                onSubmit={(event) => void submitRename(event)}
              >
                <input
                  autoFocus
                  value={renameDraft}
                  aria-label={`重命名 ${session.title}`}
                  onChange={(event) => setRenameDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") cancelRename();
                  }}
                  disabled={busy || isRunning}
                />
                <div>
                  <button
                    type="submit"
                    disabled={busy || isRunning || !renameDraft.trim()}
                  >
                    SAVE
                  </button>
                  <button
                    type="button"
                    onClick={cancelRename}
                    disabled={busy}
                  >
                    CANCEL
                  </button>
                </div>
              </form>
            );
          }
          return (
            <div
              className={`session-item ${isActive ? "is-active" : ""}`}
              key={session.sessionId}
            >
              <span className="session-accent" aria-hidden="true" />
              <button
                type="button"
                className="session-select"
                onClick={() => onSelect(session.sessionId)}
              >
                <span className="session-title">{session.title}</span>
                <span className="session-meta">
                  {session.messageCount} messages · {relativeTime(session.updatedAt)}
                </span>
              </button>
              <div className="session-actions" ref={menuSessionId === session.sessionId ? menuRef : undefined}>
                <button
                  type="button"
                  className="session-menu-button"
                  aria-label={`${session.title} 的操作`}
                  aria-expanded={menuSessionId === session.sessionId}
                  disabled={isRunning}
                  onClick={() =>
                    setMenuSessionId((current) =>
                      current === session.sessionId ? null : session.sessionId,
                    )
                  }
                >
                  <span aria-hidden="true">⋯</span>
                </button>
                {menuSessionId === session.sessionId && (
                  <div className="session-menu" role="menu">
                    <button
                      type="button"
                      role="menuitem"
                      disabled={isRunning}
                      onClick={() => beginRename(session)}
                    >
                      RENAME
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      disabled={isRunning}
                      onClick={() => {
                        if (isRunning) return;
                        setMenuSessionId(null);
                        setDeleteTarget(session);
                      }}
                    >
                      DELETE
                    </button>
                  </div>
                )}
              </div>
              {isRunning && <span className="session-running">RUNNING</span>}
            </div>
          );
        })}
      </div>
      <div className="rail-footer">
        <span className="micro-label">LOCAL RUNTIME</span>
        <p>Session history stays on your Gateway.</p>
      </div>

      {deleteTarget &&
        createPortal(
          <div className="modal-backdrop" role="presentation">
            <section
              className="confirm-dialog"
              role="alertdialog"
              aria-modal="true"
              aria-labelledby="delete-session-title"
              aria-describedby="delete-session-description"
            >
              <span className="micro-label">DESTRUCTIVE ACTION</span>
              <h2 id="delete-session-title">Delete session?</h2>
              <p id="delete-session-description">
                “{deleteTarget.title}” 的消息、summary 和附件都会被永久删除。
              </p>
              <div className="dialog-actions">
                <button
                  autoFocus
                  type="button"
                  className="dialog-cancel"
                  disabled={busy}
                  onClick={() => setDeleteTarget(null)}
                >
                  CANCEL
                </button>
                <button
                  type="button"
                  className="dialog-delete"
                  disabled={
                    busy || runningSessionIds.has(deleteTarget.sessionId)
                  }
                  onClick={() => void confirmDelete()}
                >
                  {busy ? "DELETING…" : "DELETE SESSION"}
                </button>
              </div>
            </section>
          </div>,
          document.body,
        )}
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
