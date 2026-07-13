import { describe, expect, it } from "vitest";

import { applyAgentEvent, settleRun, startRun } from "./state";
import type { AgentEvent } from "./types";

function event(type: string, payload: Record<string, unknown>): AgentEvent {
  return {
    type,
    sessionId: "session_0123456789ab",
    timestamp: "2026-07-13T00:00:00Z",
    payload,
  };
}

describe("session run state", () => {
  it("keeps pending input and concatenates streamed deltas", () => {
    let state = startRun(undefined, "request_1", "hello");
    state = applyAgentEvent(state, event("llm_delta", { delta: "你" }));
    state = applyAgentEvent(state, event("llm_delta", { delta: "好" }));

    expect(state.pendingUser).toBe("hello");
    expect(state.streamedAssistant).toBe("你好");
    expect(state.events).toHaveLength(1);
    expect(state.events[0].payload.delta).toBe("你好");
  });

  it("settles transient messages without losing the trace", () => {
    let state = startRun(undefined, "request_1", "hello");
    state = applyAgentEvent(state, event("turn_end", { status: "completed" }));
    state = settleRun(state);

    expect(state.running).toBe(false);
    expect(state.pendingUser).toBeNull();
    expect(state.events.at(-1)?.type).toBe("turn_end");
  });

  it("clears intermediate streamed text when the model requests a tool", () => {
    let state = startRun(undefined, "request_1", "inspect");
    state = applyAgentEvent(state, event("llm_delta", { delta: "checking" }));
    state = applyAgentEvent(state, event("tool_call", { name: "read_file" }));

    expect(state.streamedAssistant).toBe("");
    expect(state.events.at(-1)?.type).toBe("tool_call");
  });
});
