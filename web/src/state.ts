import type { AgentEvent, SessionRunState } from "./types";

export const EMPTY_RUN: SessionRunState = {
  requestId: null,
  pendingUser: null,
  intermediateAssistant: [],
  streamedAssistant: "",
  running: false,
  events: [],
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
    intermediateAssistant: [],
    streamedAssistant: "",
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
    const last = state.events.at(-1);
    const events =
      last?.type === "llm_delta"
        ? [
            ...state.events.slice(0, -1),
            {
              ...event,
              payload: {
                delta: `${String(last.payload.delta ?? "")}${delta}`,
              },
            },
          ]
        : [...state.events, event];
    return {
      ...state,
      streamedAssistant: state.streamedAssistant + delta,
      events,
    };
  }
  if (event.type === "tool_call") {
    const completed = state.streamedAssistant.trim();
    return {
      ...state,
      intermediateAssistant: completed
        ? [...state.intermediateAssistant, state.streamedAssistant]
        : state.intermediateAssistant,
      streamedAssistant: "",
      events: [...state.events, event],
    };
  }
  return {
    ...state,
    running: event.type === "turn_end" ? false : state.running,
    events: [...state.events, event],
  };
}

export function settleRun(
  previous: SessionRunState | undefined,
): SessionRunState {
  return {
    ...(previous ?? EMPTY_RUN),
    requestId: null,
    pendingUser: null,
    intermediateAssistant: [],
    streamedAssistant: "",
    running: false,
  };
}
