import { useRef, useState } from "react";

import type {
  AttachmentMetadata,
  CreateScheduledTaskInput,
  ScheduledTask,
  SessionSummary,
} from "../types";
import { ScheduledTasksPanel } from "./ScheduledTasksPanel";

interface Props {
  className?: string;
  attachments: AttachmentMetadata[];
  sessions: SessionSummary[];
  activeSessionId: string | null;
  tasks: ScheduledTask[];
  tasksLoading: boolean;
  disabled: boolean;
  onUpload: (file: File) => Promise<void>;
  onCreateTask: (input: CreateScheduledTaskInput) => Promise<unknown>;
  onCancelTask: (taskId: string) => Promise<unknown>;
  onClose: () => void;
}

export function InspectorPanel({
  className = "",
  attachments,
  sessions,
  activeSessionId,
  tasks,
  tasksLoading,
  disabled,
  onUpload,
  onCreateTask,
  onCancelTask,
  onClose,
}: Props) {
  const [uploading, setUploading] = useState(false);
  const [tab, setTab] = useState<"files" | "tasks">("files");
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
        <div className="inspector-title">
          <span className="micro-label">{tab === "files" ? "SESSION FILES" : "TASKS"}</span>
          <span className="file-count">{tab === "files" ? attachments.length : tasks.length}</span>
        </div>
        <button className="drawer-close" type="button" onClick={onClose} aria-label="关闭">
          ×
        </button>
      </div>

      <div className="inspector-body">
        <div className="inspector-tabs" role="tablist" aria-label="Inspector sections">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "files"}
            onClick={() => setTab("files")}
          >
            FILES
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "tasks"}
            onClick={() => setTab("tasks")}
          >
            TASKS
          </button>
        </div>
        {tab === "files" ? <div className="files-panel">
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
        </div> : (
          <ScheduledTasksPanel
            sessions={sessions}
            activeSessionId={activeSessionId}
            tasks={tasks}
            loading={tasksLoading}
            onCreate={onCreateTask}
            onCancel={onCancelTask}
          />
        )}
      </div>
    </aside>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
