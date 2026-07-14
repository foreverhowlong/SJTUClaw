// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MemoryPanel } from "./MemoryPanel";


describe("MemoryPanel", () => {
  it("creates trimmed global memory and clears the editor", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryPanel
        memories={[]}
        loading={false}
        onCreate={onCreate}
        onDelete={vi.fn()}
      />,
    );

    const editor = screen.getByLabelText("NEW MEMORY");
    await user.type(editor, "  Prefer concise answers.  ");
    await user.click(screen.getByRole("button", { name: "ADD MEMORY" }));

    expect(onCreate).toHaveBeenCalledWith("Prefer concise answers.");
    expect((editor as HTMLTextAreaElement).value).toBe("");
  });

  it("requires inline confirmation before deletion", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryPanel
        memories={[
          { memoryId: "mem_0123456789ab", content: "Use Chinese by default." },
        ]}
        loading={false}
        onCreate={vi.fn()}
        onDelete={onDelete}
      />,
    );

    await user.click(screen.getByRole("button", { name: "DELETE" }));
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByText("DELETE THIS MEMORY?")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "CONFIRM" }));
    expect(onDelete).toHaveBeenCalledWith("mem_0123456789ab");
  });
});
