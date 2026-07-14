// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { InspectorPanel } from "./InspectorPanel";


describe("InspectorPanel section selector", () => {
  it("groups real sections in a compact selector and switches content", async () => {
    const user = userEvent.setup();
    render(
      <InspectorPanel
        attachments={[]}
        sessions={[]}
        activeSessionId={null}
        tasks={[]}
        tasksLoading={false}
        memories={[]}
        memoriesLoading={false}
        disabled={false}
        workspace={null}
        onUpload={vi.fn()}
        onSetWorkspace={vi.fn()}
        onCreateTask={vi.fn()}
        onCancelTask={vi.fn()}
        onAddMemory={vi.fn()}
        onDeleteMemory={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("SESSION ATTACHMENTS")).toBeTruthy();

    const selector = screen.getByRole("button", {
      name: "选择 Inspector 栏目",
    });
    await user.click(selector);

    expect(screen.getByRole("listbox", { name: "Inspector sections" })).toBeTruthy();
    expect(screen.getByText("AUTOMATION")).toBeTruthy();
    expect(screen.getByText("AGENT")).toBeTruthy();
    await user.click(screen.getByRole("option", { name: /WORKSPACE/ }));

    expect(screen.getByText("SESSION WORKSPACE")).toBeTruthy();
    expect(selector.textContent).toContain("WORKSPACE");
    expect(selector.getAttribute("aria-expanded")).toBe("false");
  });
});
