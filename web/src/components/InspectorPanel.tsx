import { useEffect, useRef, useState } from "react";

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
  workspace: string | null;
  onUpload: (file: File) => Promise<void>;
  onSetWorkspace: (path: string | null) => Promise<void>;
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
  workspace,
  onUpload,
  onSetWorkspace,
  onCreateTask,
  onCancelTask,
  onClose,
}: Props) {
  const [uploading, setUploading] = useState(false);
  const [tab, setTab] = useState<"files" | "tasks" | "workspace">("files");
  const [workspaceDraft, setWorkspaceDraft] = useState(workspace ?? "");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => setWorkspaceDraft(workspace ?? ""), [workspace]);

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
          <span className="micro-label">{tab === "files" ? "SESSION FILES" : tab === "tasks" ? "TASKS" : "WORKSPACE"}</span>
          <span className="file-count">{tab === "files" ? attachments.length : tab === "tasks" ? tasks.length : workspace ? 1 : 0}</span>
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
          <button
            type="button"
            role="tab"
            aria-selected={tab === "workspace"}
            onClick={() => setTab("workspace")}
          >
            WORKSPACE
          </button>
        </div>
        {tab === "workspace" ? (
          <div className="files-panel workspace-panel">
            <span className="micro-label">SESSION WORKSPACE</span>
            <p className="muted-copy">当前：{workspace ?? "尚未设置"}</p>
            <input
              aria-label="Workspace path"
              value={workspaceDraft}
              onChange={(event) => setWorkspaceDraft(event.target.value)}
              placeholder="/absolute/server/path"
              disabled={disabled}
            />
            <div className="workspace-actions">
              <button
                className="workspace-set"
                type="button"
                disabled={disabled || !workspaceDraft.trim()}
                onClick={() => void onSetWorkspace(workspaceDraft.trim())}
              >
                SET WORKSPACE
              </button>
              <button
                className="workspace-clear"
                type="button"
                disabled={disabled || !workspace}
                onClick={() => void onSetWorkspace(null)}
              >
                CLEAR
              </button>
            </div>
          </div>
        ) : tab === "files" ? <div className="files-panel">
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
