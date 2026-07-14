import { useEffect, useMemo, useRef, useState } from "react";

import type {
  AttachmentMetadata,
  CreateScheduledTaskInput,
  MemoryRecord,
  ScheduledTask,
  SessionSummary,
  SkillSummary,
  SkillUsage,
} from "../types";
import { ScheduledTasksPanel } from "./ScheduledTasksPanel";
import { MemoryPanel } from "./MemoryPanel";
import { SkillsPanel } from "./SkillsPanel";

type InspectorSection = "files" | "memory" | "skills" | "tasks" | "workspace";

const SECTION_GROUPS: Array<{
  label: string;
  items: Array<{ id: InspectorSection; label: string }>;
}> = [
  {
    label: "SESSION",
    items: [
      { id: "files", label: "FILES" },
      { id: "workspace", label: "WORKSPACE" },
    ],
  },
  {
    label: "AGENT",
    items: [
      { id: "memory", label: "MEMORY" },
      { id: "skills", label: "SKILLS" },
    ],
  },
  {
    label: "AUTOMATION",
    items: [{ id: "tasks", label: "TASKS" }],
  },
];

interface Props {
  className?: string;
  attachments: AttachmentMetadata[];
  sessions: SessionSummary[];
  activeSessionId: string | null;
  tasks: ScheduledTask[];
  tasksLoading: boolean;
  memories: MemoryRecord[];
  memoriesLoading: boolean;
  disabled: boolean;
  workspace: string | null;
  skills: SkillSummary[];
  skillUsages: SkillUsage[];
  selectedSkillName: string | null;
  onUpload: (file: File) => Promise<void>;
  onSetWorkspace: (path: string | null) => Promise<void>;
  onCreateTask: (input: CreateScheduledTaskInput) => Promise<unknown>;
  onCancelTask: (taskId: string) => Promise<unknown>;
  onAddMemory: (content: string) => Promise<unknown>;
  onDeleteMemory: (memoryId: string) => Promise<unknown>;
  onSelectSkill: (name: string) => void;
  onClose: () => void;
}

export function InspectorPanel({
  className = "",
  attachments,
  sessions,
  activeSessionId,
  tasks,
  tasksLoading,
  memories,
  memoriesLoading,
  disabled,
  workspace,
  skills,
  skillUsages,
  selectedSkillName,
  onUpload,
  onSetWorkspace,
  onCreateTask,
  onCancelTask,
  onAddMemory,
  onDeleteMemory,
  onSelectSkill,
  onClose,
}: Props) {
  const [uploading, setUploading] = useState(false);
  const [section, setSection] = useState<InspectorSection>("files");
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [workspaceDraft, setWorkspaceDraft] = useState(workspace ?? "");
  const inputRef = useRef<HTMLInputElement>(null);
  const selectorRef = useRef<HTMLDivElement>(null);
  const selectorButtonRef = useRef<HTMLButtonElement>(null);

  const sectionMetadata = useMemo<Record<InspectorSection, string>>(
    () => ({
      files: String(attachments.length),
      memory: String(memories.length),
      skills: String(skills.length),
      tasks: String(tasks.length),
      workspace: workspace ? "SET" : "NOT SET",
    }),
    [attachments.length, memories.length, skills.length, tasks.length, workspace],
  );
  const activeSection = SECTION_GROUPS.flatMap((group) =>
    group.items.map((item) => ({ ...item, group: group.label })),
  ).find((item) => item.id === section)!;

  useEffect(() => setWorkspaceDraft(workspace ?? ""), [workspace]);
  useEffect(() => {
    if (!selectorOpen) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!selectorRef.current?.contains(event.target as Node)) {
        setSelectorOpen(false);
      }
    };
    window.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => window.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, [selectorOpen]);

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
          <span className="micro-label">INSPECTOR</span>
          <span className="file-count">{SECTION_GROUPS.flatMap((group) => group.items).length}</span>
        </div>
        <button className="drawer-close" type="button" onClick={onClose} aria-label="关闭">
          ×
        </button>
      </div>

      <div className="inspector-body">
        <div
          className="inspector-selector"
          ref={selectorRef}
          onKeyDown={(event) => {
            if (event.key === "Escape" && selectorOpen) {
              setSelectorOpen(false);
              selectorButtonRef.current?.focus();
            }
          }}
        >
          <button
            ref={selectorButtonRef}
            className="inspector-selector-trigger"
            type="button"
            aria-haspopup="listbox"
            aria-expanded={selectorOpen}
            aria-controls="inspector-section-menu"
            aria-label="选择 Inspector 栏目"
            onClick={() => setSelectorOpen((open) => !open)}
          >
            <span className="inspector-selector-current">
              <span className="micro-label">{activeSection.group}</span>
              <strong>{activeSection.label}</strong>
            </span>
            <span className="inspector-selector-value">
              {sectionMetadata[section]}
            </span>
            <span className="inspector-selector-chevron" aria-hidden="true">
              {selectorOpen ? "⌃" : "⌄"}
            </span>
          </button>
          {selectorOpen && (
            <div
              className="inspector-selector-menu"
              id="inspector-section-menu"
              role="listbox"
              aria-label="Inspector sections"
            >
              {SECTION_GROUPS.map((group) => (
                <div className="inspector-selector-group" key={group.label}>
                  <span className="micro-label">{group.label}</span>
                  {group.items.map((item) => (
                    <button
                      type="button"
                      role="option"
                      aria-selected={section === item.id}
                      key={item.id}
                      onClick={() => {
                        setSection(item.id);
                        setSelectorOpen(false);
                        selectorButtonRef.current?.focus();
                      }}
                    >
                      <span>{item.label}</span>
                      <span>{sectionMetadata[item.id]}</span>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="inspector-panel-content">
        {section === "workspace" ? (
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
        ) : section === "files" ? <div className="files-panel">
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
        </div> : section === "memory" ? (
          <MemoryPanel
            memories={memories}
            loading={memoriesLoading}
            onCreate={onAddMemory}
            onDelete={onDeleteMemory}
          />
        ) : section === "skills" ? (
          <SkillsPanel
            skills={skills}
            usages={skillUsages}
            selectedSkillName={selectedSkillName}
            onSelect={onSelectSkill}
          />
        ) : (
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
      </div>
    </aside>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
