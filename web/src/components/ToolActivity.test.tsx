// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToolActivity } from "./ToolActivity";

afterEach(cleanup);


describe("ToolActivity", () => {
  it("resolves a pending approval from the tool bubble", async () => {
    const resolve = vi.fn(async () => undefined);
    render(
      <ToolActivity
        item={{
          type: "tool_activity",
          callId: "call_1",
          toolName: "create_file",
          action: "创建文件",
          target: "note.txt",
          status: "awaiting_approval",
          detail: "",
          error: "",
          approval: {
            approvalId: "approval_1",
            arguments: { path: "note.txt", content: "hello" },
            workspace: "/tmp/project",
          },
        }}
        onResolveApproval={resolve}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(resolve).toHaveBeenCalledWith("approval_1", true, "");
    expect(screen.getByText("SCOPE")).toBeTruthy();
    expect(screen.getByText("/tmp/project")).toBeTruthy();

    const details = screen.getByText("Detail").closest("details");
    expect(details?.open).toBe(false);

    await userEvent.click(screen.getByText("Detail"));
    expect(details?.open).toBe(true);
    expect(screen.getByText(/"content": "hello"/)).toBeTruthy();
  });

  it("renders a Gateway download link from a completed tool", () => {
    render(
      <ToolActivity
        item={{
          type: "tool_activity",
          callId: "call_2",
          toolName: "create_download",
          action: "准备下载",
          target: "report.md",
          status: "succeeded",
          detail: "report.md",
          error: "",
          download: {
            downloadId: "download_1",
            downloadUrl: "/api/downloads/download_1",
            filename: "report.md",
            expiresAt: "2026-07-14T00:00:00+00:00",
          },
        }}
      />,
    );

    expect(screen.getByText("READY TO DOWNLOAD")).toBeTruthy();
    const link = screen.getByRole("link", { name: "Download report.md" });
    expect(link.getAttribute("href")).toBe("/api/downloads/download_1");
  });
});
