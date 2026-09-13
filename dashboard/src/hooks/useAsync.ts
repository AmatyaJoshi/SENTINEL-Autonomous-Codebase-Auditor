import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "@/lib/api";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  setData: (updater: T | ((prev: T | null) => T | null)) => void;
}

/**
 * Minimal data-fetching hook with optional polling. `deps` re-run the fetch.
 * Keeps previous data visible while refetching (no flash of skeleton).
 */
export function useAsync<T>(fn: () => Promise<T>, deps: readonly unknown[], opts: { pollMs?: number; enabled?: boolean } = {}): AsyncState<T> {
  const [data, setDataState] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const enabled = opts.enabled ?? true;

  const reload = useCallback(async () => {
    if (!enabled) return;
    try {
      const d = await fnRef.current();
      setDataState(d);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const d = await fnRef.current();
        if (alive) {
          setDataState(d);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(errorMessage(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    let timer: ReturnType<typeof setInterval> | null = null;
    if (opts.pollMs && enabled) {
      timer = setInterval(() => {
        if (document.visibilityState === "visible") void reload();
      }, opts.pollMs);
    }
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, opts.pollMs, enabled, reload]);

  const setData = useCallback<AsyncState<T>["setData"]>((updater) => {
    setDataState((prev) => (typeof updater === "function" ? (updater as (p: T | null) => T | null)(prev) : updater));
  }, []);

  return { data, loading, error, reload, setData };
}
