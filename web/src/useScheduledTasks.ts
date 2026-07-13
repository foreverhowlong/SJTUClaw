import { useCallback, useEffect, useState } from "react";

import {
  cancelScheduledTask,
  createScheduledTask,
  listScheduledTasks,
} from "./api";
import type { CreateScheduledTaskInput, ScheduledTask } from "./types";

const REFRESH_INTERVAL_MS = 3000;

export function useScheduledTasks(onError: (message: string) => void) {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const items = await listScheduledTasks();
    setTasks(items);
    return items;
  }, []);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        await refresh();
      } catch (reason) {
        if (active) onError(errorMessage(reason));
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    const timer = window.setInterval(() => {
      void refresh().catch((reason) => {
        if (active) onError(errorMessage(reason));
      });
    }, REFRESH_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [onError, refresh]);

  const create = useCallback(
    async (input: CreateScheduledTaskInput) => {
      try {
        const task = await createScheduledTask(input);
        await refresh();
        return task;
      } catch (reason) {
        onError(errorMessage(reason));
        throw reason;
      }
    },
    [onError, refresh],
  );

  const cancel = useCallback(
    async (taskId: string) => {
      try {
        const task = await cancelScheduledTask(taskId);
        await refresh();
        return task;
      } catch (reason) {
        onError(errorMessage(reason));
        throw reason;
      }
    },
    [onError, refresh],
  );

  return { tasks, loading, refresh, create, cancel };
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "任务请求失败。";
}
