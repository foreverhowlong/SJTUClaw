// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { SessionDetail, SessionSummary } from "./types";

const mocks = vi.hoisted(() => ({
  sessions: [] as SessionSummary[],
  details: {} as Record<string, SessionDetail>,
  listSessions: vi.fn(),
  createSession: vi.fn(),
  getSession: vi.fn(),
  deleteSession: vi.fn(),
}));

vi.mock("./api", () => ({
  listSessions: mocks.listSessions,
  createSession: mocks.createSession,
  getSession: mocks.getSession,
  deleteSession: mocks.deleteSession,
  renameSession: vi.fn(),
  listAttachments: vi.fn().mockResolvedValue([]),
  uploadAttachment: vi.fn(),
}));

vi.mock("./useGatewaySocket", () => ({
  useGatewaySocket: () => ({ connection: "connected", sendTurn: vi.fn() }),
}));

import App from "./App";

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeSession(sessionId: string, title: string): SessionDetail {
  return {
    sessionId,
    title,
    messageCount: 0,
    createdAt: "2026-07-13T00:00:00Z",
    updatedAt: "2026-07-13T00:00:00Z",
    revision: 0,
    summary: "",
    messages: [],
    timeline: [],
  };
}

describe("App session deletion", () => {
  it("creates and selects a replacement after deleting the last session", async () => {
    const user = userEvent.setup();
    const original = makeSession("session_0123456789ab", "Only session");
    const replacement = makeSession("session_bbbbbbbbbbbb", "Replacement");
    mocks.sessions = [original];
    mocks.details = { [original.sessionId]: original };
    mocks.listSessions.mockImplementation(async () => [...mocks.sessions]);
    mocks.getSession.mockImplementation(async (sessionId: string) => mocks.details[sessionId]);
    mocks.deleteSession.mockImplementation(async (sessionId: string) => {
      mocks.sessions = mocks.sessions.filter((item) => item.sessionId !== sessionId);
      delete mocks.details[sessionId];
    });
    mocks.createSession.mockImplementation(async () => {
      mocks.sessions = [replacement];
      mocks.details[replacement.sessionId] = replacement;
      return replacement;
    });

    render(<App />);
    await screen.findByRole("heading", { name: "Only session" });
    expect(screen.queryByText("ACTIVITY")).toBeNull();
    expect(screen.getByText("SESSION FILES")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Only session 的操作" }));
    await user.click(screen.getByRole("menuitem", { name: "DELETE" }));
    await user.click(screen.getByRole("button", { name: "DELETE SESSION" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Replacement" })).toBeTruthy(),
    );
    expect(mocks.createSession).toHaveBeenCalledTimes(1);
  });
});
