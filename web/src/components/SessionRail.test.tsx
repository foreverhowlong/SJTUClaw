// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SessionSummary } from "../types";
import { SessionRail } from "./SessionRail";

afterEach(cleanup);

const session: SessionSummary = {
  sessionId: "session_0123456789ab",
  title: "Alpha",
  messageCount: 2,
  createdAt: "2026-07-13T00:00:00Z",
  updatedAt: new Date().toISOString(),
};

function renderRail(overrides: Partial<Parameters<typeof SessionRail>[0]> = {}) {
  const props: Parameters<typeof SessionRail>[0] = {
    sessions: [session],
    activeSessionId: session.sessionId,
    loading: false,
    runningSessionIds: new Set(),
    onSelect: vi.fn(),
    onNew: vi.fn(),
    onRename: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn().mockResolvedValue(undefined),
    onClose: vi.fn(),
    ...overrides,
  };
  render(<SessionRail {...props} />);
  return props;
}

describe("SessionRail", () => {
  it("places New Session below the Sessions heading and supports inline rename", async () => {
    const user = userEvent.setup();
    const props = renderRail();
    const heading = screen.getByRole("heading", { name: "Sessions" });
    const newButton = screen.getByRole("button", { name: /NEW SESSION/ });
    expect(
      heading.compareDocumentPosition(newButton) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Alpha 的操作" }));
    await user.click(screen.getByRole("menuitem", { name: "RENAME" }));
    const input = screen.getByRole("textbox", { name: "重命名 Alpha" });
    await user.clear(input);
    await user.type(input, "Renamed{Enter}");

    await waitFor(() =>
      expect(props.onRename).toHaveBeenCalledWith(session.sessionId, "Renamed"),
    );
  });

  it("requires custom confirmation before deletion", async () => {
    const user = userEvent.setup();
    const props = renderRail();

    await user.click(screen.getByRole("button", { name: "Alpha 的操作" }));
    await user.click(screen.getByRole("menuitem", { name: "DELETE" }));
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    expect(props.onDelete).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "DELETE SESSION" }));
    await waitFor(() =>
      expect(props.onDelete).toHaveBeenCalledWith(session.sessionId),
    );
  });

  it("disables session actions while that session is running", () => {
    renderRail({ runningSessionIds: new Set([session.sessionId]) });

    expect(
      (screen.getByRole("button", { name: "Alpha 的操作" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.getByText("RUNNING")).toBeTruthy();
  });
});
