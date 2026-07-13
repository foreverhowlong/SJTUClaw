// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { EMPTY_RUN } from "../state";
import type { SessionDetail } from "../types";
import { ConversationPane } from "./ConversationPane";

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(cleanup);

function detail(messages: SessionDetail["messages"]): SessionDetail {
  return {
    sessionId: "session_0123456789ab",
    title: "Markdown session",
    messageCount: messages.length,
    createdAt: "2026-07-13T00:00:00Z",
    updatedAt: "2026-07-13T00:00:00Z",
    revision: 1,
    summary: "",
    messages,
  };
}

describe("ConversationPane", () => {
  it("renders assistant GFM safely while keeping user text literal", () => {
    render(
      <ConversationPane
        detail={detail([
          { role: "user", content: "**literal user**" },
          {
            role: "assistant",
            content:
              "## Result\n\n- one\n- two\n\n~~old~~\n\n<script>unsafe()</script>\n\n[docs](https://example.com)",
          },
        ])}
        run={EMPTY_RUN}
        connection="connected"
        loading={false}
        onSend={vi.fn()}
      />,
    );

    expect(screen.getByText("**literal user**")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Result" })).toBeTruthy();
    expect(screen.getByText("old").tagName).toBe("DEL");
    expect(screen.queryByText("unsafe()", { exact: false })).toBeNull();
    const link = screen.getByRole("link", { name: "docs" });
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("shows persisted and live tool preludes as working notes", () => {
    render(
      <ConversationPane
        detail={detail([
          { role: "user", content: "inspect" },
          {
            role: "assistant",
            content: "I will inspect **README**.",
            tool_calls: [{}],
          },
          { role: "assistant", content: "Final answer." },
        ])}
        run={{
          ...EMPTY_RUN,
          intermediateAssistant: ["Now checking `task6.MD`."],
          running: true,
        }}
        connection="connected"
        loading={false}
        onSend={vi.fn()}
      />,
    );

    expect(screen.getAllByText("CLAW / WORKING NOTE")).toHaveLength(2);
    expect(screen.getByText("README").tagName).toBe("STRONG");
    expect(screen.getByText("task6.MD").tagName).toBe("CODE");
    expect(screen.getByText("Final answer.")).toBeTruthy();
  });
});
