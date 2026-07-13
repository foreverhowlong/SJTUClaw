import type {
  AgentEvent,
  SessionRunState,
  TimelineItem,
  ToolActivityItem,
} from "./types";

export const EMPTY_RUN: SessionRunState = {
  requestId: null,
  pendingUser: null,
  liveTimeline: [],
  running: false,
};

export function startRun(
  previous: SessionRunState | undefined,
  requestId: string,
  message: string,
): SessionRunState {
  return {
    ...(previous ?? EMPTY_RUN),
    requestId,
    pendingUser: message,
    liveTimeline: [],
    running: true,
  };
}

export function applyAgentEvent(
  previous: SessionRunState | undefined,
  event: AgentEvent,
): SessionRunState {
  const state = previous ?? EMPTY_RUN;
  if (event.type === "llm_delta") {
    const delta = typeof event.payload.delta === "string" ? event.payload.delta : "";
    if (!delta) return state;
    const last = state.liveTimeline.at(-1);
    const liveTimeline =
      last?.type === "assistant_message"
        ? [
            ...state.liveTimeline.slice(0, -1),
            { ...last, content: last.content + delta },
          ]
        : [
            ...state.liveTimeline,
            { type: "assistant_message" as const, content: delta },
          ];
    return {
      ...state,
      liveTimeline,
    };
  }
  if (event.type === "tool_call") {
    const liveTimeline = [...state.liveTimeline];
    const last = liveTimeline.at(-1);
    if (last?.type === "assistant_message") {
      liveTimeline[liveTimeline.length - 1] = {
        type: "working_note",
        content: last.content,
      };
    }
    const tool = timelineTool(event);
    if (tool) liveTimeline.push(tool);
    return {
      ...state,
      liveTimeline,
    };
  }
  if (
    event.type === "tool_result" ||
    event.type === "approval_required" ||
    event.type === "approval_resolved"
  ) {
    const tool = timelineTool(event);
    if (!tool) return state;
    return {
      ...state,
      liveTimeline: replaceTool(state.liveTimeline, tool),
    };
  }
  if (event.type === "warning" || event.type === "error") {
    const content = event.payload.message;
    if (typeof content !== "string" || !content.trim()) return state;
    return {
      ...state,
      liveTimeline: [
        ...state.liveTimeline,
        { type: "runtime_notice", level: event.type, content },
      ],
    };
  }
  return {
    ...state,
    running: event.type === "turn_end" ? false : state.running,
  };
}

export function settleRun(
  previous: SessionRunState | undefined,
): SessionRunState {
  return {
    ...(previous ?? EMPTY_RUN),
    requestId: null,
    pendingUser: null,
    liveTimeline: [],
    running: false,
  };
}

function timelineTool(event: AgentEvent): ToolActivityItem | null {
  const value = event.payload.timelineItem;
  if (!value || typeof value !== "object") return null;
  const item = value as Partial<ToolActivityItem>;
  if (
    item.type !== "tool_activity" ||
    typeof item.callId !== "string" ||
    typeof item.toolName !== "string" ||
    typeof item.action !== "string" ||
    typeof item.target !== "string" ||
    typeof item.detail !== "string" ||
    typeof item.error !== "string" ||
    !["running", "succeeded", "failed", "awaiting_approval"].includes(
      String(item.status),
    )
  ) {
    return null;
  }
  return item as ToolActivityItem;
}

function replaceTool(
  timeline: TimelineItem[],
  next: ToolActivityItem,
): TimelineItem[] {
  const index = timeline.findIndex(
    (item) => item.type === "tool_activity" && item.callId === next.callId,
  );
  if (index < 0) return [...timeline, next];
  return timeline.map((item, itemIndex) => (itemIndex === index ? next : item));
}
