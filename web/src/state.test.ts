import { describe, expect, it } from "vitest";

import { applyAgentEvent, settleRun, startRun } from "./state";
import type { AgentEvent, ToolActivityItem } from "./types";

function event(type: string, payload: Record<string, unknown>): AgentEvent {
  return {
    type,
    sessionId: "session_0123456789ab",
    timestamp: "2026-07-13T00:00:00Z",
    payload,
  };
}

function tool(
  callId: string,
  status: ToolActivityItem["status"] = "running",
): ToolActivityItem {
  return {
    type: "tool_activity",
    callId,
    toolName: "read_file",
    action: "读取文件",
    target: "README.md",
    status,
    detail: status === "succeeded" ? "12 字符" : "",
    error: status === "failed" ? "文件不存在。" : "",
  };
}

describe("session run state", () => {
  it("keeps pending input and concatenates streamed deltas", () => {
    let state = startRun(undefined, "request_1", "hello");
    state = applyAgentEvent(state, event("llm_delta", { delta: "你" }));
    state = applyAgentEvent(state, event("llm_delta", { delta: "好" }));

    expect(state.pendingUser).toBe("hello");
    expect(state.liveTimeline).toEqual([
      { type: "assistant_message", content: "你好" },
    ]);
  });

  it("settles all transient timeline content", () => {
    let state = startRun(undefined, "request_1", "hello");
    state = applyAgentEvent(state, event("llm_delta", { delta: "reply" }));
    state = settleRun(state);

    expect(state.running).toBe(false);
    expect(state.pendingUser).toBeNull();
    expect(state.liveTimeline).toEqual([]);
  });

  it("freezes streamed text and inserts a distinct tool activity", () => {
    let state = startRun(undefined, "request_1", "inspect");
    state = applyAgentEvent(state, event("llm_delta", { delta: "checking" }));
    state = applyAgentEvent(
      state,
      event("tool_call", { timelineItem: tool("call_1") }),
    );

    expect(state.liveTimeline).toEqual([
      { type: "working_note", content: "checking" },
      tool("call_1"),
    ]);
  });

  it("keeps multiple working notes and tools in causal order", () => {
    let state = startRun(undefined, "request_1", "inspect");
    state = applyAgentEvent(state, event("llm_delta", { delta: "first" }));
    state = applyAgentEvent(
      state,
      event("tool_call", { timelineItem: tool("call_1") }),
    );
    state = applyAgentEvent(state, event("llm_delta", { delta: "second" }));
    state = applyAgentEvent(
      state,
      event("tool_call", { timelineItem: tool("call_2") }),
    );
    state = applyAgentEvent(state, event("llm_delta", { delta: "final" }));

    expect(state.liveTimeline.map((item) => item.type)).toEqual([
      "working_note",
      "tool_activity",
      "working_note",
      "tool_activity",
      "assistant_message",
    ]);
  });

  it("updates a tool by call id and ignores transport-only events", () => {
    let state = startRun(undefined, "request_1", "inspect");
    state = applyAgentEvent(
      state,
      event("tool_call", { timelineItem: tool("call_1") }),
    );
    state = applyAgentEvent(state, event("turn_start", {}));
    state = applyAgentEvent(
      state,
      event("tool_result", { timelineItem: tool("call_1", "succeeded") }),
    );

    expect(state.liveTimeline).toEqual([tool("call_1", "succeeded")]);
  });

  it("keeps user-relevant runtime errors but not turn lifecycle noise", () => {
    let state = startRun(undefined, "request_1", "inspect");
    state = applyAgentEvent(
      state,
      event("error", { message: "Provider unavailable" }),
    );
    state = applyAgentEvent(
      state,
      event("turn_end", { status: "failed" }),
    );

    expect(state.liveTimeline).toEqual([
      {
        type: "runtime_notice",
        level: "error",
        content: "Provider unavailable",
      },
    ]);
    expect(state.running).toBe(false);
  });

  it("renders selected skill source and reason as live activity", () => {
    let state = startRun(undefined, "request_1", "write report");
    state = applyAgentEvent(
      state,
      event("skill_selected", {
        name: "course-report",
        source: "auto",
        reason: "The task is a course report.",
      }),
    );

    expect(state.liveTimeline).toEqual([
      {
        type: "skill_activity",
        name: "course-report",
        source: "auto",
        reason: "The task is a course report.",
      },
    ]);
  });
});
