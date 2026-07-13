import { FormEvent, useEffect, useMemo, useState } from "react";

import type {
  CreateScheduledTaskInput,
  ScheduledTask,
  SessionSummary,
} from "../types";

interface Props {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  tasks: ScheduledTask[];
  loading: boolean;
  onCreate: (input: CreateScheduledTaskInput) => Promise<unknown>;
  onCancel: (taskId: string) => Promise<unknown>;
}

export function ScheduledTasksPanel({
  sessions,
  activeSessionId,
  tasks,
  loading,
  onCreate,
  onCancel,
}: Props) {
  const [content, setContent] = useState("");
  const [sessionId, setSessionId] = useState(activeSessionId ?? "");
  const [scheduleType, setScheduleType] = useState<"once" | "interval">("once");
  const [triggerAt, setTriggerAt] = useState("");
  const [intervalValue, setIntervalValue] = useState("1");
  const [intervalUnit, setIntervalUnit] = useState<"minutes" | "hours" | "days">(
    "hours",
  );
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");
  const sessionNames = useMemo(
    () => new Map(sessions.map((session) => [session.sessionId, session.title])),
    [sessions],
  );

  useEffect(() => {
    if (activeSessionId && !sessions.some((item) => item.sessionId === sessionId)) {
      setSessionId(activeSessionId);
    }
  }, [activeSessionId, sessionId, sessions]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFormError("");
    const when = new Date(triggerAt);
    if (!content.trim() || !sessionId || !triggerAt || Number.isNaN(when.getTime())) {
      setFormError("请填写任务内容、所属 session 和有效的首次触发时间。");
      return;
    }
    let input: CreateScheduledTaskInput;
    if (scheduleType === "once") {
      input = {
        sessionId,
        content: content.trim(),
        schedule: { type: "once", runAt: when.toISOString() },
      };
    } else {
      const interval = Number(intervalValue);
      if (!Number.isInteger(interval) || interval <= 0) {
        setFormError("重复间隔必须是大于 0 的整数。");
        return;
      }
      input = {
        sessionId,
        content: content.trim(),
        schedule: {
          type: "interval",
          startAt: when.toISOString(),
          intervalSeconds: interval * unitSeconds(intervalUnit),
        },
      };
    }
    setSubmitting(true);
    try {
      await onCreate(input);
      setContent("");
      setTriggerAt("");
    } catch {
      // The shared error banner contains the server error.
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="tasks-panel">
      <form className="task-form" onSubmit={(event) => void submit(event)}>
        <div className="files-intro">
          <span className="micro-label">NEW SCHEDULE</span>
          <p>到期后任务内容会作为用户消息进入所选 session。</p>
        </div>
        <label>
          <span>INSTRUCTION</span>
          <textarea
            value={content}
            onChange={(event) => setContent(event.target.value)}
            placeholder="例如：总结这个 session 的最新进展"
          />
        </label>
        <label>
          <span>SESSION</span>
          <select value={sessionId} onChange={(event) => setSessionId(event.target.value)}>
            {sessions.map((session) => (
              <option key={session.sessionId} value={session.sessionId}>
                {session.title}
              </option>
            ))}
          </select>
        </label>
        <div className="task-form-row">
          <label>
            <span>TYPE</span>
            <select
              value={scheduleType}
              onChange={(event) =>
                setScheduleType(event.target.value as "once" | "interval")
              }
            >
              <option value="once">ONCE</option>
              <option value="interval">INTERVAL</option>
            </select>
          </label>
          <label>
            <span>FIRST RUN</span>
            <input
              type="datetime-local"
              value={triggerAt}
              onChange={(event) => setTriggerAt(event.target.value)}
            />
          </label>
        </div>
        {scheduleType === "interval" && (
          <div className="task-form-row">
            <label>
              <span>EVERY</span>
              <input
                type="number"
                min="1"
                step="1"
                value={intervalValue}
                onChange={(event) => setIntervalValue(event.target.value)}
              />
            </label>
            <label>
              <span>UNIT</span>
              <select
                value={intervalUnit}
                onChange={(event) =>
                  setIntervalUnit(event.target.value as typeof intervalUnit)
                }
              >
                <option value="minutes">MINUTES</option>
                <option value="hours">HOURS</option>
                <option value="days">DAYS</option>
              </select>
            </label>
          </div>
        )}
        {formError && <p className="task-form-error">{formError}</p>}
        <button
          className="upload-button"
          type="submit"
          disabled={submitting || sessions.length === 0}
        >
          <span>{submitting ? "CREATING…" : "CREATE TASK"}</span>
          <span aria-hidden="true">＋</span>
        </button>
      </form>

      <div className="task-list">
        <div className="task-list-heading">
          <span className="micro-label">SCHEDULED TASKS</span>
          <span>{tasks.length}</span>
        </div>
        {loading && <p className="muted-copy">正在加载任务…</p>}
        {!loading && tasks.length === 0 && (
          <p className="muted-copy">还没有定时任务。</p>
        )}
        {tasks.map((task) => (
          <TaskCard
            key={task.taskId}
            task={task}
            sessionName={sessionNames.get(task.sessionId) ?? task.sessionId}
            onCancel={onCancel}
          />
        ))}
      </div>
    </div>
  );
}

function TaskCard({
  task,
  sessionName,
  onCancel,
}: {
  task: ScheduledTask;
  sessionName: string;
  onCancel: (taskId: string) => Promise<unknown>;
}) {
  const [cancelling, setCancelling] = useState(false);
  const canCancel = task.nextRunAt !== null || task.status === "running";
  const cancel = async () => {
    setCancelling(true);
    try {
      await onCancel(task.taskId);
    } catch {
      // The shared error banner contains the server error.
    } finally {
      setCancelling(false);
    }
  };
  return (
    <article className="task-card">
      <div className="task-card-topline">
        <span className={`task-status task-status-${task.status}`}>{task.status}</span>
        <span>{scheduleLabel(task)}</span>
      </div>
      <strong>{task.content}</strong>
      <dl>
        <div><dt>SESSION</dt><dd>{sessionName}</dd></div>
        <div><dt>NEXT</dt><dd>{formatTime(task.nextRunAt)}</dd></div>
      </dl>
      {task.history.length > 0 && (
        <details className="task-history">
          <summary>EXECUTION HISTORY · {task.history.length}</summary>
          {[...task.history].reverse().map((execution) => (
            <div className="task-execution" key={execution.executionId}>
              <span>{execution.status.toUpperCase()} · {formatTime(execution.startedAt)}</span>
              {execution.assistantReply && <p>{execution.assistantReply}</p>}
              {execution.errorMessage && <p>{execution.errorMessage}</p>}
            </div>
          ))}
        </details>
      )}
      {canCancel && task.status !== "cancelled" && (
        <button
          className="underline-button"
          type="button"
          disabled={cancelling}
          onClick={() => void cancel()}
        >
          {cancelling ? "CANCELLING…" : "CANCEL FUTURE RUNS"}
        </button>
      )}
    </article>
  );
}

function scheduleLabel(task: ScheduledTask): string {
  if (task.schedule.type === "once") return "ONCE";
  return `EVERY ${formatInterval(task.schedule.intervalSeconds)}`;
}

function formatInterval(seconds: number): string {
  if (seconds % 86400 === 0) return `${seconds / 86400}D`;
  if (seconds % 3600 === 0) return `${seconds / 3600}H`;
  if (seconds % 60 === 0) return `${seconds / 60}M`;
  return `${seconds}S`;
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN") : "—";
}

function unitSeconds(unit: "minutes" | "hours" | "days"): number {
  if (unit === "minutes") return 60;
  if (unit === "hours") return 3600;
  return 86400;
}
