import { useEffect, useRef, useState } from "react";

import type {
  ConnectionState,
  ConversationMessage,
  SessionDetail,
  SessionRunState,
} from "../types";

interface Props {
  detail?: SessionDetail;
  run: SessionRunState;
  connection: ConnectionState;
  loading: boolean;
  onSend: (content: string) => void;
}

export function ConversationPane({
  detail,
  run,
  connection,
  loading,
  onSend,
}: Props) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const messages = visibleMessages(detail?.messages ?? []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [messages.length, run.pendingUser, run.streamedAssistant]);

  const submit = () => {
    const content = draft.trim();
    if (!content || run.running || connection !== "connected") return;
    onSend(content);
    setDraft("");
  };

  return (
    <section className="conversation-pane">
      <div className="conversation-header">
        <div>
          <span className="micro-label">CONVERSATION</span>
          <h1>{detail?.title ?? "New conversation"}</h1>
        </div>
        <span className="revision-label">
          REV {detail?.revision ?? 0}
        </span>
      </div>

      <div className="message-scroll" aria-live="polite">
        {loading && <p className="muted-copy">正在恢复对话历史…</p>}
        {!loading && messages.length === 0 && !run.pendingUser && (
          <div className="conversation-empty">
            <span className="micro-label">CLAW / READY</span>
            <h2>What should we<br />work through?</h2>
            <p>
              从一个问题开始。Claw 会沿用当前 session 的 context、memory、
              compaction 与只读工具。
            </p>
          </div>
        )}
        {messages.map((message, index) => (
          <MessageBubble key={`${message.role}-${index}`} message={message} />
        ))}
        {run.pendingUser && (
          <MessageBubble
            message={{ role: "user", content: run.pendingUser }}
            pending
          />
        )}
        {run.streamedAssistant && (
          <MessageBubble
            message={{ role: "assistant", content: run.streamedAssistant }}
            streaming={run.running}
          />
        )}
        {run.running && !run.streamedAssistant && (
          <div className="agent-waiting" aria-label="Agent 正在处理">
            <span /> <span /> <span />
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="composer-wrap">
        <label className="composer" aria-label="发送消息">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            placeholder={
              connection === "connected"
                ? "Message Claw…"
                : "Waiting for Gateway…"
            }
            rows={2}
            disabled={!detail || run.running}
          />
          <div className="composer-footer">
            <span className="composer-hint">ENTER TO SEND · SHIFT+ENTER FOR NEW LINE</span>
            <button
              className="send-button"
              type="button"
              onClick={submit}
              disabled={
                !draft.trim() || run.running || connection !== "connected"
              }
            >
              {run.running ? "RUNNING" : "SEND"}
              <span aria-hidden="true">↗</span>
            </button>
          </div>
        </label>
      </div>
    </section>
  );
}

function visibleMessages(messages: ConversationMessage[]) {
  return messages.filter(
    (message) =>
      (message.role === "user" ||
        (message.role === "assistant" && !message.tool_calls)) &&
      typeof message.content === "string" &&
      message.content.trim(),
  );
}

function MessageBubble({
  message,
  pending = false,
  streaming = false,
}: {
  message: ConversationMessage;
  pending?: boolean;
  streaming?: boolean;
}) {
  const user = message.role === "user";
  return (
    <article className={`message-row ${user ? "message-user" : "message-agent"}`}>
      <div className="message-label">
        <span className="micro-label">{user ? "YOU" : "CLAW"}</span>
        {pending && <span className="pending-label">PENDING</span>}
      </div>
      <div className="message-content">
        {message.content}
        {streaming && <span className="stream-caret" aria-hidden="true" />}
      </div>
    </article>
  );
}
