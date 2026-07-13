import { useRef, useState } from "react";

import type { AgentEvent, AttachmentMetadata } from "../types";

interface Props {
  className?: string;
  events: AgentEvent[];
  attachments: AttachmentMetadata[];
  disabled: boolean;
  onUpload: (file: File) => Promise<void>;
  onClose: () => void;
}

export function InspectorPanel({
  className = "",
  events,
  attachments,
  disabled,
  onUpload,
  onClose,
}: Props) {
  const [tab, setTab] = useState<"activity" | "files">("activity");
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const selectFile = async (file?: File) => {
    if (!file) return;
    setUploading(true);
    try {
      await onUpload(file);
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <aside className={`inspector drawer-panel ${className}`}>
      <div className="inspector-topline">
        <div className="tab-list" role="tablist" aria-label="Inspector">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "activity"}
            className={tab === "activity" ? "is-active" : ""}
            onClick={() => setTab("activity")}
          >
            ACTIVITY
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "files"}
            className={tab === "files" ? "is-active" : ""}
            onClick={() => setTab("files")}
          >
            FILES <span>{attachments.length}</span>
          </button>
        </div>
        <button className="drawer-close" type="button" onClick={onClose} aria-label="关闭">
          ×
        </button>
      </div>

      {tab === "activity" ? (
        <AgentTrace events={events} />
      ) : (
        <div className="files-panel">
          <div className="files-intro">
            <span className="micro-label">SESSION ATTACHMENTS</span>
            <p>附件只属于当前 session，不会成为 workspace 文件。</p>
          </div>
          <input
            ref={inputRef}
            className="visually-hidden"
            type="file"
            onChange={(event) => void selectFile(event.target.files?.[0])}
          />
          <button
            type="button"
            className="upload-button"
            disabled={disabled || uploading}
            onClick={() => inputRef.current?.click()}
          >
            <span>{uploading ? "UPLOADING…" : "UPLOAD FILE"}</span>
            <span aria-hidden="true">＋</span>
          </button>
          <div className="file-list">
            {attachments.length === 0 && (
              <p className="muted-copy">这个 session 还没有附件。</p>
            )}
            {attachments.map((file) => (
              <article className="file-card" key={file.attachmentId}>
                <span className="file-glyph" aria-hidden="true">□</span>
                <div>
                  <strong>{file.filename}</strong>
                  <span>{formatBytes(file.size)} · {file.contentType}</span>
                  <span>{new Date(file.uploadedAt).toLocaleString("zh-CN")}</span>
                </div>
              </article>
            ))}
          </div>
        </div>
      )}
    </aside>
  );
}

function AgentTrace({ events }: { events: AgentEvent[] }) {
  if (events.length === 0) {
    return (
      <div className="trace-empty">
        <div className="trace-orbit" aria-hidden="true"><span /></div>
        <span className="micro-label">NO ACTIVITY YET</span>
        <p>发送消息后，这里会显示可观察的 runtime 事件，而不是隐藏思维链。</p>
      </div>
    );
  }

  return (
    <div className="trace-list" aria-live="polite">
      {events.map((event, index) => (
        <EventCard key={`${event.timestamp}-${index}`} event={event} />
      ))}
    </div>
  );
}

function EventCard({ event }: { event: AgentEvent }) {
  const label = EVENT_LABELS[event.type] ?? event.type.replaceAll("_", " ").toUpperCase();
  const isError = event.type === "error" || event.type === "warning";
  return (
    <article className={`event-card event-${event.type} ${isError ? "event-dark" : ""}`}>
      <span className="event-node" aria-hidden="true" />
      <div className="event-heading">
        <span className="micro-label">{label}</span>
        <time>{formatTime(event.timestamp)}</time>
      </div>
      <EventPayload event={event} />
    </article>
  );
}

function EventPayload({ event }: { event: AgentEvent }) {
  const payload = event.payload;
  if (event.type === "turn_start") {
    return <p>Turn accepted by the shared runtime.</p>;
  }
  if (event.type === "llm_delta") {
    return <p>Assistant stream · {String(payload.delta ?? "").length} characters</p>;
  }
  if (event.type === "llm_message") {
    return <p>Final response committed to session history.</p>;
  }
  if (event.type === "turn_end") {
    return <p>Status · {String(payload.status ?? "unknown")}</p>;
  }
  if (event.type === "tool_call") {
    return (
      <details>
        <summary>{String(payload.name ?? "tool")}</summary>
        <pre>{formatValue(payload.arguments)}</pre>
      </details>
    );
  }
  if (event.type === "tool_result") {
    return (
      <details>
        <summary>{String(payload.name ?? "tool")} · {payload.ok ? "OK" : "FAILED"}</summary>
        <pre>{formatValue(payload.truncated ? payload.preview : payload.result ?? payload.error)}</pre>
      </details>
    );
  }
  if (event.type === "error" || event.type === "warning") {
    return <p>{String(payload.message ?? "Runtime notice")}</p>;
  }
  return (
    <details>
      <summary>View details</summary>
      <pre>{JSON.stringify(payload, null, 2)}</pre>
    </details>
  );
}

const EVENT_LABELS: Record<string, string> = {
  turn_start: "TURN STARTED",
  llm_delta: "RESPONSE STREAM",
  llm_message: "RESPONSE COMMITTED",
  tool_call: "TOOL CALL",
  tool_result: "TOOL RESULT",
  approval_required: "APPROVAL REQUIRED",
  approval_resolved: "APPROVAL RESOLVED",
  compaction_started: "COMPACTION",
  compaction_done: "COMPACTION DONE",
  warning: "WARNING",
  error: "ERROR",
  turn_end: "TURN COMPLETED",
};

function formatValue(value: unknown): string {
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2);
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
