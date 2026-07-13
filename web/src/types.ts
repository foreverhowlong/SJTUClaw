export type ConnectionState = "connected" | "reconnecting" | "offline";

export interface SessionSummary {
  sessionId: string;
  title: string;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface ConversationMessage {
  role: "system" | "user" | "assistant" | "tool";
  content?: string | null;
  name?: string;
  tool_call_id?: string;
  tool_calls?: unknown[];
}

export interface SessionDetail extends SessionSummary {
  revision: number;
  summary: string;
  messages: ConversationMessage[];
  timeline: PersistedTimelineItem[];
}

export interface TextTimelineItem {
  type: "user_message" | "assistant_message" | "working_note";
  content: string;
}

export type ToolActivityStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "awaiting_approval";

export interface ToolActivityItem {
  type: "tool_activity";
  callId: string;
  toolName: string;
  action: string;
  target: string;
  status: ToolActivityStatus;
  detail: string;
  error: string;
}

export interface RuntimeNoticeItem {
  type: "runtime_notice";
  level: "warning" | "error";
  content: string;
}

export type PersistedTimelineItem = TextTimelineItem | ToolActivityItem;
export type TimelineItem = PersistedTimelineItem | RuntimeNoticeItem;

export interface AttachmentMetadata {
  attachmentId: string;
  filename: string;
  size: number;
  contentType: string;
  uploadedAt: string;
}

export interface AgentEvent {
  type: string;
  sessionId: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

export type GatewayMessage =
  | {
      type: "session_resolved";
      requestId: string;
      created: boolean;
      session: SessionDetail;
    }
  | {
      type: "agent_event";
      requestId: string;
      event: AgentEvent;
    }
  | {
      type: "gateway_error";
      requestId: string;
      error: { code: string; message: string };
    };

export interface SessionRunState {
  requestId: string | null;
  pendingUser: string | null;
  liveTimeline: TimelineItem[];
  running: boolean;
}
