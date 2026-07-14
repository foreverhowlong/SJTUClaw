import type {
  AttachmentMetadata,
  CreateScheduledTaskInput,
  MemoryRecord,
  ScheduledTask,
  SessionDetail,
  SessionSummary,
  SkillSummary,
  SkillUsage,
} from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const message = body?.error?.message ?? `请求失败 (${response.status})`;
    throw new Error(message);
  }
  return body as T;
}

export async function listSessions(): Promise<SessionSummary[]> {
  const body = await request<{ sessions: SessionSummary[] }>("/api/sessions");
  return body.sessions;
}

export function createSession(title = "新会话"): Promise<SessionDetail> {
  return request<SessionDetail>("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export function getSession(sessionId: string): Promise<SessionDetail> {
  return request<SessionDetail>(`/api/sessions/${sessionId}`);
}

export function renameSession(
  sessionId: string,
  title: string,
): Promise<SessionDetail> {
  return request<SessionDetail>(`/api/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export async function deleteSession(sessionId: string): Promise<void> {
  const response = await fetch(`/api/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? `请求失败 (${response.status})`);
  }
}

export async function listAttachments(
  sessionId: string,
): Promise<AttachmentMetadata[]> {
  const body = await request<{ attachments: AttachmentMetadata[] }>(
    `/api/sessions/${sessionId}/attachments`,
  );
  return body.attachments;
}

export function uploadAttachment(
  sessionId: string,
  file: File,
): Promise<AttachmentMetadata> {
  const data = new FormData();
  data.append("file", file);
  return request<AttachmentMetadata>(
    `/api/sessions/${sessionId}/attachments`,
    { method: "POST", body: data },
  );
}

export function setWorkspace(
  sessionId: string,
  path: string | null,
): Promise<{ sessionId: string; workspace: string | null }> {
  return request(`/api/sessions/${sessionId}/workspace`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

export function resolveApproval(
  approvalId: string,
  approved: boolean,
  reason = "",
): Promise<unknown> {
  return request(`/api/approvals/${approvalId}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved, reason }),
  });
}

export async function listScheduledTasks(): Promise<ScheduledTask[]> {
  const body = await request<{ tasks: ScheduledTask[] }>("/api/tasks");
  return body.tasks;
}

export function createScheduledTask(
  input: CreateScheduledTaskInput,
): Promise<ScheduledTask> {
  return request<ScheduledTask>("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function cancelScheduledTask(taskId: string): Promise<ScheduledTask> {
  return request<ScheduledTask>(`/api/tasks/${taskId}/cancel`, {
    method: "POST",
  });
}

export async function listMemories(): Promise<MemoryRecord[]> {
  const body = await request<{ memories: MemoryRecord[] }>("/api/memories");
  return body.memories;
}

export function createMemory(content: string): Promise<MemoryRecord> {
  return request<MemoryRecord>("/api/memories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export function deleteMemory(memoryId: string): Promise<void> {
  return request<void>(`/api/memories/${memoryId}`, { method: "DELETE" });
}

export async function listSkills(): Promise<SkillSummary[]> {
  const body = await request<{ skills: SkillSummary[] }>("/api/skills");
  return body.skills;
}

export async function listSkillUsages(sessionId: string): Promise<SkillUsage[]> {
  const body = await request<{ usages: SkillUsage[] }>(
    `/api/sessions/${sessionId}/skill-usages`,
  );
  return body.usages;
}
