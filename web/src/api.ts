import type {
  AttachmentMetadata,
  SessionDetail,
  SessionSummary,
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
