import { useCallback, useEffect, useState } from "react";

import { createMemory, deleteMemory, listMemories } from "./api";
import type { MemoryRecord } from "./types";

export function useMemories(onError: (message: string) => void) {
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const records = await listMemories();
    setMemories(records);
    return records;
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
    return () => {
      active = false;
    };
  }, [onError, refresh]);

  const create = useCallback(
    async (content: string) => {
      try {
        const record = await createMemory(content);
        await refresh();
        return record;
      } catch (reason) {
        onError(errorMessage(reason));
        throw reason;
      }
    },
    [onError, refresh],
  );

  const remove = useCallback(
    async (memoryId: string) => {
      try {
        await deleteMemory(memoryId);
        await refresh();
      } catch (reason) {
        onError(errorMessage(reason));
        throw reason;
      }
    },
    [onError, refresh],
  );

  return { memories, loading, refresh, create, remove };
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Memory 请求失败。";
}
