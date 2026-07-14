import { useState } from "react";
import type { FormEvent } from "react";

import type { MemoryRecord } from "../types";

interface Props {
  memories: MemoryRecord[];
  loading: boolean;
  onCreate: (content: string) => Promise<unknown>;
  onDelete: (memoryId: string) => Promise<unknown>;
}

export function MemoryPanel({ memories, loading, onCreate, onDelete }: Props) {
  const [draft, setDraft] = useState("");
  const [creating, setCreating] = useState(false);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const content = draft.trim();
    if (!content || creating) return;
    setCreating(true);
    try {
      await onCreate(content);
      setDraft("");
    } catch {
      // The shared error banner contains the server error.
    } finally {
      setCreating(false);
    }
  };

  const remove = async (memoryId: string) => {
    setDeletingId(memoryId);
    try {
      await onDelete(memoryId);
      setConfirmingId(null);
    } catch {
      // The shared error banner contains the server error.
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="memory-panel">
      <div className="memory-intro">
        <span className="micro-label">GLOBAL MEMORY</span>
        <p>这些内容会加入所有 Sessions 的模型上下文。</p>
      </div>

      <form className="memory-form" onSubmit={(event) => void submit(event)}>
        <label htmlFor="memory-content">NEW MEMORY</label>
        <textarea
          id="memory-content"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="记录需要跨 Session 保留的事实或偏好…"
          rows={4}
        />
        <button type="submit" disabled={creating || !draft.trim()}>
          {creating ? "ADDING…" : "ADD MEMORY"}
        </button>
      </form>

      <div className="memory-list" aria-live="polite">
        <div className="memory-list-heading">
          <span className="micro-label">SAVED MEMORIES</span>
          <span>{memories.length}</span>
        </div>
        {loading ? (
          <p className="muted-copy">正在加载 Memory…</p>
        ) : memories.length === 0 ? (
          <p className="muted-copy">还没有全局 Memory。</p>
        ) : (
          memories.map((memory) => (
            <article className="memory-card" key={memory.memoryId}>
              <span className="memory-id">{memory.memoryId}</span>
              <p>{memory.content}</p>
              {confirmingId === memory.memoryId ? (
                <div className="memory-confirmation">
                  <span>DELETE THIS MEMORY?</span>
                  <div>
                    <button
                      type="button"
                      disabled={deletingId === memory.memoryId}
                      onClick={() => setConfirmingId(null)}
                    >
                      CANCEL
                    </button>
                    <button
                      type="button"
                      disabled={deletingId === memory.memoryId}
                      onClick={() => void remove(memory.memoryId)}
                    >
                      {deletingId === memory.memoryId ? "DELETING…" : "CONFIRM"}
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  className="memory-delete"
                  type="button"
                  onClick={() => setConfirmingId(memory.memoryId)}
                >
                  DELETE
                </button>
              )}
            </article>
          ))
        )}
      </div>
    </div>
  );
}
