import { useRef, useState } from "react";

import type { AttachmentMetadata } from "../types";

interface Props {
  className?: string;
  attachments: AttachmentMetadata[];
  disabled: boolean;
  onUpload: (file: File) => Promise<void>;
  onClose: () => void;
}

export function InspectorPanel({
  className = "",
  attachments,
  disabled,
  onUpload,
  onClose,
}: Props) {
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
        <div className="inspector-title">
          <span className="micro-label">SESSION FILES</span>
          <span className="file-count">{attachments.length}</span>
        </div>
        <button className="drawer-close" type="button" onClick={onClose} aria-label="关闭">
          ×
        </button>
      </div>

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
    </aside>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
