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
  source?: "scheduled_task";
}

export interface SessionDetail extends SessionSummary {
  revision: number;
  summary: string;
  workspace: string | null;
  messages: ConversationMessage[];
  timeline: PersistedTimelineItem[];
}

export interface TextTimelineItem {
  type: "user_message" | "assistant_message" | "working_note";
  content: string;
  source?: "scheduled_task";
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
  approval?: {
    approvalId: string;
    arguments: Record<string, unknown>;
    workspace: string | null;
  };
  download?: {
    downloadId: string;
    downloadUrl: string;
    filename: string;
    expiresAt: string;
  };
}

export interface RuntimeNoticeItem {
  type: "runtime_notice";
  level: "warning" | "error";
  content: string;
}

export interface SkillActivityItem {
  type: "skill_activity";
  name: string;
  source: "explicit" | "auto";
  reason: string;
}

export type PersistedTimelineItem = TextTimelineItem | ToolActivityItem;
export type TimelineItem =
  | PersistedTimelineItem
  | RuntimeNoticeItem
  | SkillActivityItem;

export interface AttachmentMetadata {
  attachmentId: string;
  filename: string;
  size: number;
  contentType: string;
  uploadedAt: string;
}

export interface MemoryRecord {
  memoryId: string;
  content: string;
}

export interface SkillSummary {
  name: string;
  description: string;
  origin: "builtin" | "local";
}

export interface SkillUsage {
  usageId: string;
  turnId: string;
  skillName: string;
  sessionId: string;
  task: string;
  source: "explicit" | "auto";
  reason: string;
  usedAt: string;
  outcome: "completed" | "failed" | "interrupted";
  finalOutput: string;
}

export type ScheduledTaskStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type TaskSchedule =
  | { type: "once"; runAt: string }
  | {
      type: "interval";
      startAt: string;
      intervalSeconds: number;
    };

export interface TaskExecution {
  executionId: string;
  scheduledFor: string;
  startedAt: string;
  finishedAt: string | null;
  status: "running" | "succeeded" | "failed";
  assistantReply: string;
  errorCode: string;
  errorMessage: string;
}

export interface ScheduledTask {
  schemaVersion: 1;
  taskId: string;
  sessionId: string;
  content: string;
  schedule: TaskSchedule;
  nextRunAt: string | null;
  status: ScheduledTaskStatus;
  createdAt: string;
  updatedAt: string;
  revision: number;
  history: TaskExecution[];
}

export interface CreateScheduledTaskInput {
  sessionId: string;
  content: string;
  schedule: TaskSchedule;
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
    }
  | {
      type: "session_updated";
      sessionId: string;
      reason: "scheduled_task";
    };

export interface SessionRunState {
  requestId: string | null;
  pendingUser: string | null;
  liveTimeline: TimelineItem[];
  running: boolean;
}
