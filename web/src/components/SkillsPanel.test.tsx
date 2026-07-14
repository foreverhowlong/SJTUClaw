// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SkillsPanel } from "./SkillsPanel";

describe("SkillsPanel", () => {
  it("selects a skill and shows session usage reason", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <SkillsPanel
        skills={[
          {
            name: "course-report",
            description: "Write course reports.",
            origin: "builtin",
          },
        ]}
        usages={[
          {
            usageId: "usage_1",
            turnId: "turn_1",
            skillName: "course-report",
            sessionId: "session_0123456789ab",
            task: "write",
            source: "auto",
            reason: "The task is a course report.",
            usedAt: "2026-07-14T00:00:00Z",
            outcome: "completed",
            finalOutput: "done",
          },
        ]}
        selectedSkillName={null}
        onSelect={onSelect}
      />,
    );

    await user.click(screen.getByRole("button", { name: "USE NEXT" }));
    expect(onSelect).toHaveBeenCalledWith("course-report");
    expect(screen.getByText("The task is a course report.")).toBeTruthy();
  });
});
