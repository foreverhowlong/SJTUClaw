// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { EMPTY_RUN } from "../state";
import type { PersistedTimelineItem, SessionDetail } from "../types";
import { ConversationPane } from "./ConversationPane";

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(cleanup);

function detail(timeline: PersistedTimelineItem[]): SessionDetail {
  return {
    sessionId: "session_0123456789ab",
    title: "Markdown session",
    messageCount: timeline.length,
    createdAt: "2026-07-13T00:00:00Z",
    updatedAt: "2026-07-13T00:00:00Z",
    revision: 1,
    summary: "",
    messages: [],
    timeline,
  };
}

const completedTool: PersistedTimelineItem = {
  type: "tool_activity",
  callId: "call_1",
  toolName: "read_attachment",
  action: "读取附件",
  target: "task6.MD",
  status: "succeeded",
  detail: "1,024 字符",
  error: "",
};

describe("ConversationPane", () => {
  it("renders safe GFM and LaTeX for assistant content only", () => {
    render(
      <ConversationPane
        detail={detail([
          { type: "user_message", content: "**literal** and $x^2$" },
          {
            type: "assistant_message",
            content:
              "## Result\n\n- one\n- two\n\nInline $E=mc^2$.\n\n$$x^2+y^2=z^2$$\n\n<script>unsafe()</script>\n\n[docs](https://example.com)",
          },
        ])}
        run={EMPTY_RUN}
        connection="connected"
        loading={false}
        onSend={vi.fn()}
      />,
    );

    expect(screen.getByText("**literal** and $x^2$")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Result" })).toBeTruthy();
    expect(document.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("unsafe()", { exact: false })).toBeNull();
    const link = screen.getByRole("link", { name: "docs" });
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("labels scheduled instructions separately from manual user messages", () => {
    render(
      <ConversationPane
        detail={detail([
          {
            type: "user_message",
            content: "生成日报",
            source: "scheduled_task",
          },
          { type: "assistant_message", content: "日报已生成。" },
        ])}
        run={EMPTY_RUN}
        connection="connected"
        loading={false}
        onSend={vi.fn()}
      />,
    );

    expect(screen.getByText("SCHEDULED TASK")).toBeTruthy();
    expect(screen.queryByText("YOU")).toBeNull();
    expect(screen.getByText("生成日报").closest(".message-scheduled")).toBeTruthy();
  });

  it("renders persisted and live tools between working notes and replies", () => {
    render(
      <ConversationPane
        detail={detail([
          { type: "user_message", content: "inspect" },
          { type: "working_note", content: "I will inspect **README**." },
          completedTool,
          { type: "assistant_message", content: "Final answer." },
        ])}
        run={{
          ...EMPTY_RUN,
          liveTimeline: [
            { type: "working_note", content: "Now checking `DESIGN.md`." },
            {
              ...completedTool,
              callId: "call_2",
              target: "DESIGN.md",
              status: "running",
              detail: "",
            },
          ],
          running: true,
        }}
        connection="connected"
        loading={false}
        onSend={vi.fn()}
      />,
    );

    expect(screen.getAllByText("CLAW / WORKING NOTE")).toHaveLength(2);
    expect(screen.getByText("README").tagName).toBe("STRONG");
    expect(
      screen.getAllByText("DESIGN.md").some((element) => element.tagName === "CODE"),
    ).toBe(true);
    expect(screen.getByLabelText("读取附件 DONE")).toBeTruthy();
    expect(screen.getByLabelText("读取附件 RUNNING")).toBeTruthy();
    expect(screen.getByText(/1,024 字符/)).toBeTruthy();
    expect(screen.getByText("Final answer.")).toBeTruthy();
    expect(screen.queryByText("TURN STARTED")).toBeNull();
    expect(screen.queryByText("RESPONSE STREAM")).toBeNull();
  });
});
