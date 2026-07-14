import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createSession,
  deleteSession as deleteSessionRequest,
  getSession,
  listAttachments,
  listSessions,
  renameSession as renameSessionRequest,
  resolveApproval,
  setWorkspace,
  uploadAttachment,
} from "./api";
import { ConversationPane } from "./components/ConversationPane";
import { InspectorPanel } from "./components/InspectorPanel";
import { SessionRail } from "./components/SessionRail";
import { applyAgentEvent, EMPTY_RUN, settleRun, startRun } from "./state";
import type {
  AttachmentMetadata,
  GatewayMessage,
  SessionDetail,
  SessionRunState,
  SessionSummary,
} from "./types";
import { useGatewaySocket } from "./useGatewaySocket";
import { useScheduledTasks } from "./useScheduledTasks";

export default function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, SessionDetail>>({});
  const [attachments, setAttachments] = useState<
    Record<string, AttachmentMetadata[]>
  >({});
  const [runs, setRuns] = useState<Record<string, SessionRunState>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const reportTaskError = useCallback((message: string) => setError(message), []);
  const scheduled = useScheduledTasks(reportTaskError);

  const refreshSessions = useCallback(async () => {
    const items = await listSessions();
    setSessions(items);
    return items;
  }, []);

  const loadSession = useCallback(async (sessionId: string) => {
    const [detail, files] = await Promise.all([
      getSession(sessionId),
      listAttachments(sessionId),
    ]);
    setDetails((previous) => ({ ...previous, [sessionId]: detail }));
    setAttachments((previous) => ({ ...previous, [sessionId]: files }));
  }, []);

  const selectSession = useCallback(
    async (sessionId: string) => {
      setActiveSessionId(sessionId);
      setLeftOpen(false);
      setError(null);
      try {
        await loadSession(sessionId);
      } catch (reason) {
        setError(errorMessage(reason));
      }
    },
    [loadSession],
  );

  useEffect(() => {
    let cancelled = false;
    const bootstrap = async () => {
      try {
        let items = await refreshSessions();
        if (items.length === 0) {
          const created = await createSession();
          items = await refreshSessions();
          if (!cancelled) {
            setActiveSessionId(created.sessionId);
            setDetails({ [created.sessionId]: created });
            setAttachments({ [created.sessionId]: [] });
          }
        } else if (!cancelled) {
          setActiveSessionId(items[0].sessionId);
          await loadSession(items[0].sessionId);
        }
      } catch (reason) {
        if (!cancelled) setError(errorMessage(reason));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [loadSession, refreshSessions]);

  const handleGatewayMessage = useCallback(
    (message: GatewayMessage) => {
      if (message.type === "session_updated") {
        void Promise.all([
          loadSession(message.sessionId),
          refreshSessions(),
        ]).catch((reason) => setError(errorMessage(reason)));
        return;
      }

      if (message.type === "gateway_error") {
        setError(message.error.message);
        setRuns((previous) => {
          const next = { ...previous };
          for (const [sessionId, run] of Object.entries(next)) {
            if (run.requestId === message.requestId) {
              next[sessionId] = settleRun(run);
            }
          }
          return next;
        });
        return;
      }

      if (message.type === "session_resolved") {
        const session = message.session;
        setDetails((previous) => ({ ...previous, [session.sessionId]: session }));
        if (message.created) {
          setActiveSessionId(session.sessionId);
          void refreshSessions();
        }
        return;
      }

      const event = message.event;
      setRuns((previous) => ({
        ...previous,
        [event.sessionId]: applyAgentEvent(previous[event.sessionId], event),
      }));
      if (event.type === "error" && typeof event.payload.message === "string") {
        setError(event.payload.message);
      }
      if (event.type === "turn_end") {
        void Promise.all([
          loadSession(event.sessionId),
          refreshSessions(),
        ])
          .catch((reason) => setError(errorMessage(reason)))
          .finally(() => {
            setRuns((previous) => ({
              ...previous,
              [event.sessionId]: settleRun(previous[event.sessionId]),
            }));
          });
      }
    },
    [loadSession, refreshSessions],
  );

  const { connection, sendTurn } = useGatewaySocket(handleGatewayMessage);

  const handleNewSession = useCallback(async () => {
    try {
      const created = await createSession();
      setDetails((previous) => ({ ...previous, [created.sessionId]: created }));
      setAttachments((previous) => ({ ...previous, [created.sessionId]: [] }));
      setActiveSessionId(created.sessionId);
      setLeftOpen(false);
      await refreshSessions();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }, [refreshSessions]);

  const handleRenameSession = useCallback(
    async (sessionId: string, title: string) => {
      try {
        const renamed = await renameSessionRequest(sessionId, title);
        setDetails((previous) => ({ ...previous, [sessionId]: renamed }));
        await refreshSessions();
      } catch (reason) {
        setError(errorMessage(reason));
        throw reason;
      }
    },
    [refreshSessions],
  );

  const handleDeleteSession = useCallback(
    async (sessionId: string) => {
      try {
        await deleteSessionRequest(sessionId);
        setDetails((previous) => omitKey(previous, sessionId));
        setAttachments((previous) => omitKey(previous, sessionId));
        setRuns((previous) => omitKey(previous, sessionId));
        const remaining = await refreshSessions();
        if (activeSessionId !== sessionId) return;

        if (remaining.length > 0) {
          setActiveSessionId(remaining[0].sessionId);
          await loadSession(remaining[0].sessionId);
          return;
        }

        const created = await createSession();
        setActiveSessionId(created.sessionId);
        setDetails((previous) => ({ ...previous, [created.sessionId]: created }));
        setAttachments((previous) => ({
          ...previous,
          [created.sessionId]: [],
        }));
        await refreshSessions();
      } catch (reason) {
        setError(errorMessage(reason));
        throw reason;
      }
    },
    [activeSessionId, loadSession, refreshSessions],
  );

  const handleSend = useCallback(
    (content: string) => {
      if (!activeSessionId) return;
      const requestId = `request_${crypto.randomUUID().replaceAll("-", "")}`;
      setError(null);
      setRuns((previous) => ({
        ...previous,
        [activeSessionId]: startRun(
          previous[activeSessionId],
          requestId,
          content,
        ),
      }));
      try {
        sendTurn(requestId, activeSessionId, content);
      } catch (reason) {
        setRuns((previous) => ({
          ...previous,
          [activeSessionId]: settleRun(previous[activeSessionId]),
        }));
        setError(errorMessage(reason));
      }
    },
    [activeSessionId, sendTurn],
  );

  const handleUpload = useCallback(
    async (file: File) => {
      if (!activeSessionId) return;
      try {
        await uploadAttachment(activeSessionId, file);
        const files = await listAttachments(activeSessionId);
        setAttachments((previous) => ({
          ...previous,
          [activeSessionId]: files,
        }));
      } catch (reason) {
        setError(errorMessage(reason));
      }
    },
    [activeSessionId],
  );

  const handleResolveApproval = useCallback(async (approvalId: string, approved: boolean, reason: string) => {
    try {
      await resolveApproval(approvalId, approved, reason);
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }, []);

  const handleSetWorkspace = useCallback(async (path: string | null) => {
    if (!activeSessionId) return;
    try {
      await setWorkspace(activeSessionId, path);
      await loadSession(activeSessionId);
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }, [activeSessionId, loadSession]);

  const activeDetail = activeSessionId ? details[activeSessionId] : undefined;
  const activeRun = activeSessionId
    ? runs[activeSessionId] ?? EMPTY_RUN
    : EMPTY_RUN;
  const activeAttachments = activeSessionId
    ? attachments[activeSessionId] ?? []
    : [];
  const currentTitle = activeDetail?.title ?? "正在准备会话";
  const connectionLabel = useMemo(
    () => connection.toUpperCase(),
    [connection],
  );
  const runningSessionIds = useMemo(
    () =>
      new Set(
        Object.entries(runs)
          .filter(([, run]) => run.running)
          .map(([sessionId]) => sessionId),
      ),
    [runs],
  );

  return (
    <main className="page-shell">
      <section className="app-shell" aria-label="SJTUClaw Agent Command Center">
        <header className="top-bar">
          <div className="brand-block">
            <button
              className="mobile-toggle"
              type="button"
              onClick={() => setLeftOpen(true)}
              aria-label="打开 session 列表"
            >
              ≡
            </button>
            <div>
              <div className="brand">SJTUClaw</div>
              <div className="micro-label">AGENT COMMAND CENTER</div>
            </div>
          </div>
          <div className="current-session-heading">
            <span className="micro-label">CURRENT SESSION</span>
            <strong>{currentTitle}</strong>
          </div>
          <div className="top-actions">
            <span className={`connection-pill connection-${connection}`}>
              <span aria-hidden="true" className="connection-dot" />
              {connectionLabel}
            </span>
            <button
              className="mobile-toggle"
              type="button"
              onClick={() => setRightOpen(true)}
              aria-label="打开 Inspector"
            >
              ◫
            </button>
          </div>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            <span><strong>ERROR</strong> {error}</span>
            <button type="button" onClick={() => setError(null)} aria-label="关闭错误提示">
              ×
            </button>
          </div>
        )}

        <div className="workspace-grid">
          <SessionRail
            className={leftOpen ? "is-open" : ""}
            sessions={sessions}
            activeSessionId={activeSessionId}
            loading={loading}
            onSelect={selectSession}
            onNew={handleNewSession}
            onRename={handleRenameSession}
            onDelete={handleDeleteSession}
            runningSessionIds={runningSessionIds}
            onClose={() => setLeftOpen(false)}
          />
          <ConversationPane
            key={activeSessionId}
            detail={activeDetail}
            run={activeRun}
            connection={connection}
            loading={loading}
            onSend={handleSend}
            onResolveApproval={handleResolveApproval}
          />
          <InspectorPanel
            className={rightOpen ? "is-open" : ""}
            attachments={activeAttachments}
            sessions={sessions}
            activeSessionId={activeSessionId}
            tasks={scheduled.tasks}
            tasksLoading={scheduled.loading}
            disabled={!activeSessionId}
            workspace={activeDetail?.workspace ?? null}
            onUpload={handleUpload}
            onSetWorkspace={handleSetWorkspace}
            onCreateTask={scheduled.create}
            onCancelTask={scheduled.cancel}
            onClose={() => setRightOpen(false)}
          />
        </div>
        {(leftOpen || rightOpen) && (
          <button
            className="drawer-backdrop"
            type="button"
            aria-label="关闭面板"
            onClick={() => {
              setLeftOpen(false);
              setRightOpen(false);
            }}
          />
        )}
      </section>
    </main>
  );
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "发生未知错误。";
}

function omitKey<T>(source: Record<string, T>, key: string): Record<string, T> {
  const next = { ...source };
  delete next[key];
  return next;
}
