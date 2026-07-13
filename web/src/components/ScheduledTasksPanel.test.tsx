// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ScheduledTask, SessionSummary } from "../types";
import { ScheduledTasksPanel } from "./ScheduledTasksPanel";


const session: SessionSummary = {
  sessionId: "session_0123456789ab",
  title: "Research",
  messageCount: 0,
  createdAt: "2026-07-14T00:00:00Z",
  updatedAt: "2026-07-14T00:00:00Z",
};

const task: ScheduledTask = {
  schemaVersion: 1,
  taskId: "task_0123456789ab",
  sessionId: session.sessionId,
  content: "Summarize progress",
  schedule: { type: "interval", startAt: "2026-07-15T00:00:00Z", intervalSeconds: 3600 },
  nextRunAt: "2026-07-15T01:00:00Z",
  status: "failed",
  createdAt: "2026-07-14T00:00:00Z",
  updatedAt: "2026-07-15T00:00:10Z",
  revision: 2,
  history: [
    {
      executionId: "execution_0123456789ab",
      scheduledFor: "2026-07-15T00:00:00Z",
      startedAt: "2026-07-15T00:00:00Z",
      finishedAt: "2026-07-15T00:00:10Z",
      status: "failed",
      assistantReply: "",
      errorCode: "llm_error",
      errorMessage: "LLM unavailable",
    },
  ],
};

afterEach(() => cleanup());

describe("ScheduledTasksPanel", () => {
  it("shows task ownership, history, next run and cancels future runs", async () => {
    const user = userEvent.setup();
    const cancel = vi.fn().mockResolvedValue(undefined);
    render(
      <ScheduledTasksPanel
        sessions={[session]}
        activeSessionId={session.sessionId}
        tasks={[task]}
        loading={false}
        onCreate={vi.fn()}
        onCancel={cancel}
      />,
    );

    expect(screen.getByText("Summarize progress")).toBeTruthy();
    expect(screen.getAllByText("Research")).toHaveLength(2);
    await user.click(screen.getByText("EXECUTION HISTORY · 1"));
    expect(screen.getByText("LLM unavailable")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "CANCEL FUTURE RUNS" }));
    await waitFor(() => expect(cancel).toHaveBeenCalledWith(task.taskId));
  });

  it("builds an interval schedule with an explicit first run", async () => {
    const user = userEvent.setup();
    const create = vi.fn().mockResolvedValue(undefined);
    render(
      <ScheduledTasksPanel
        sessions={[session]}
        activeSessionId={session.sessionId}
        tasks={[]}
        loading={false}
        onCreate={create}
        onCancel={vi.fn()}
      />,
    );

    await user.type(screen.getByLabelText("INSTRUCTION"), "Check source");
    await user.selectOptions(screen.getByLabelText("TYPE"), "interval");
    fireEvent.change(screen.getByLabelText("FIRST RUN"), {
      target: { value: "2099-01-01T10:00" },
    });
    fireEvent.change(screen.getByLabelText("EVERY"), { target: { value: "5" } });
    await user.selectOptions(screen.getByLabelText("UNIT"), "minutes");
    await user.click(screen.getByRole("button", { name: "CREATE TASK" }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0]).toEqual({
      sessionId: session.sessionId,
      content: "Check source",
      schedule: {
        type: "interval",
        startAt: new Date("2099-01-01T10:00").toISOString(),
        intervalSeconds: 300,
      },
    });
  });
});
