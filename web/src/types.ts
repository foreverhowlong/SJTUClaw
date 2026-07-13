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
}

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
  intermediateAssistant: string[];
  streamedAssistant: string;
  running: boolean;
  events: AgentEvent[];
}
