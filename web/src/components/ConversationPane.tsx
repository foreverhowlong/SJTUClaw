import { useEffect, useRef, useState } from "react";
import rehypeKatex from "rehype-katex";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import type {
  CompactionResult,
  ConnectionState,
  SessionDetail,
  SessionRunState,
  TimelineItem,
} from "../types";
import { ToolActivity } from "./ToolActivity";

interface Props {
  detail?: SessionDetail;
  run: SessionRunState;
  connection: ConnectionState;
  loading: boolean;
  onSend: (content: string) => void;
  onCompact?: () => void;
  compacting?: boolean;
  compactionResult?: CompactionResult;
  onResolveApproval?: (approvalId: string, approved: boolean, reason: string) => Promise<void>;
  selectedSkillName?: string | null;
  onClearSkill?: () => void;
}

export function ConversationPane({
  detail,
  run,
  connection,
  loading,
  onSend,
  onCompact,
  compacting = false,
  compactionResult,
  onResolveApproval,
  selectedSkillName,
  onClearSkill,
}: Props) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const timeline = detail?.timeline ?? [];
  const lastLiveItem = run.liveTimeline.at(-1);
  const hasRunningTool = run.liveTimeline.some(
    (item) =>
      item.type === "tool_activity" &&
      (item.status === "running" || item.status === "awaiting_approval"),
  );

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [timeline.length, run.pendingUser, run.liveTimeline]);

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
        <div className="conversation-header-actions">
          <button
            className="compact-button"
            type="button"
            onClick={onCompact}
            disabled={!detail || run.running || compacting}
          >
            {compacting ? "COMPACTING…" : "COMPACT"}
          </button>
          <span className="revision-label">
            REV {detail?.revision ?? 0}
          </span>
        </div>
      </div>

      <div className="message-scroll" aria-live="polite">
        {compactionResult && (
          <div
            className={`compaction-notice compaction-${compactionResult.status}`}
            role="status"
          >
            <span className="micro-label">
              COMPACTION / {compactionResult.status.toUpperCase()}
            </span>
            <p>{compactionMessage(compactionResult)}</p>
          </div>
        )}
        {detail?.summary && (
          <article className="session-summary-card">
            <span className="micro-label">OLDER MESSAGES / SUMMARY</span>
            <div className="session-summary-content">
              <AssistantMarkdown content={detail.summary} />
            </div>
          </article>
        )}
        {loading && <p className="muted-copy">正在恢复对话历史…</p>}
        {!loading && timeline.length === 0 && !run.pendingUser && (
          <div className="conversation-empty">
            <span className="micro-label">CLAW / READY</span>
            <h2>What should we<br />work through?</h2>
          </div>
        )}
        {timeline.map((item, index) => (
          <TimelineEntry
            key={timelineKey(item, index, "persisted")}
            item={item}
            onResolveApproval={onResolveApproval}
          />
        ))}
        {run.pendingUser && (
          <MessageBubble content={run.pendingUser} user pending />
        )}
        {run.liveTimeline.map((item, index) => (
          <TimelineEntry
            key={timelineKey(item, index, "live")}
            item={item}
            onResolveApproval={onResolveApproval}
            streaming={
              run.running &&
              index === run.liveTimeline.length - 1 &&
              item.type === "assistant_message"
            }
          />
        ))}
        {run.running &&
          lastLiveItem?.type !== "assistant_message" &&
          !hasRunningTool && (
            <div className="agent-waiting" aria-label="Agent 正在处理">
              <span /> <span /> <span />
            </div>
          )}
        <div ref={endRef} />
      </div>

      <div className="composer-wrap">
        {selectedSkillName && (
          <div className="selected-skill-pill">
            <span>SKILL / {selectedSkillName}</span>
            <button type="button" onClick={onClearSkill} aria-label="取消使用 Skill">×</button>
          </div>
        )}
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

function compactionMessage(result: CompactionResult): string {
  if (result.status === "compacted") {
    return `${result.oldMessageCount} 条旧消息已写入 summary，保留 ${result.recentMessageCount} 条活跃消息。`;
  }
  return result.detail;
}

function TimelineEntry({
  item,
  streaming = false,
  onResolveApproval,
}: {
  item: TimelineItem;
  streaming?: boolean;
  onResolveApproval?: (approvalId: string, approved: boolean, reason: string) => Promise<void>;
}) {
  if (item.type === "tool_activity") return <ToolActivity item={item} onResolveApproval={onResolveApproval} />;
  if (item.type === "runtime_notice") {
    return (
      <article className={`runtime-notice runtime-notice-${item.level}`}>
        <span className="micro-label">RUNTIME / {item.level}</span>
        <p>{item.content}</p>
      </article>
    );
  }
  if (item.type === "skill_activity") {
    return (
      <article className="skill-activity">
        <span className="micro-label">SKILL / {item.source}</span>
        <strong>{item.name}</strong>
        <p>{item.reason}</p>
      </article>
    );
  }
  return (
    <MessageBubble
      content={item.content}
      user={item.type === "user_message"}
      scheduled={item.source === "scheduled_task"}
      workingNote={item.type === "working_note"}
      streaming={streaming}
    />
  );
}

function timelineKey(
  item: TimelineItem,
  index: number,
  scope: "persisted" | "live",
): string {
  return item.type === "tool_activity"
    ? `${scope}-tool-${item.callId}`
    : `${scope}-${item.type}-${index}`;
}

function MessageBubble({
  content,
  user = false,
  pending = false,
  streaming = false,
  workingNote = false,
  scheduled = false,
}: {
  content: string;
  user?: boolean;
  pending?: boolean;
  streaming?: boolean;
  workingNote?: boolean;
  scheduled?: boolean;
}) {
  return (
    <article
      className={`message-row ${user ? "message-user" : "message-agent"}${scheduled ? " message-scheduled" : ""}`}
    >
      <div className="message-label">
        <span className="micro-label">
          {user
            ? scheduled
              ? "SCHEDULED TASK"
              : "YOU"
            : workingNote
              ? "CLAW / WORKING NOTE"
              : "CLAW"}
        </span>
        {pending && <span className="pending-label">PENDING</span>}
      </div>
      <div className="message-content">
        {user ? (
          content
        ) : (
          <AssistantMarkdown content={content} />
        )}
        {streaming && <span className="stream-caret" aria-hidden="true" />}
      </div>
    </article>
  );
}

function AssistantMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[
        [
          rehypeKatex,
          { throwOnError: false, trust: false, strict: false },
        ],
      ]}
      skipHtml
      components={{
        a: ({ children, ...props }) => (
          <a {...props} target="_blank" rel="noreferrer noopener">
            {children}
          </a>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
