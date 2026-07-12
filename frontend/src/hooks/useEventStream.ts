import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { LiveEnvelope } from "../types";

/**
 * Subscribe to the backend SSE stream and invalidate the relevant React Query caches when a
 * live run/job update arrives — sub-second dashboard refresh without polling (plan §5.1).
 * Returns whether the stream is currently connected, for the header's live indicator.
 */
export function useEventStream(): { connected: boolean } {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const source = new EventSource("/api/v1/events/stream");
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);

    // Coalesce a burst of live ticks into at most one invalidation pass per window, scoped to the
    // repos/runs actually touched — a webhook storm on one repo shouldn't refetch every dashboard
    // query (the old code invalidated ["runs"]/["jobs"] wholesale on every single tick). Only
    // *active* queries refetch; passive ones are just marked stale.
    const FLUSH_MS = 2000;
    let flushTimer: ReturnType<typeof setTimeout> | null = null;
    const touchedRepos = new Set<number>();
    const touchedRuns = new Set<number>();
    let runsUnscoped = false; // a run tick with no repo_id → refresh the "all repos" view to be safe
    let jobsUnscoped = false;

    const flush = () => {
      flushTimer = null;
      if (touchedRepos.size || runsUnscoped) {
        queryClient.invalidateQueries({
          queryKey: ["runs"],
          refetchType: "active",
          predicate: (q) => {
            const repoId = q.queryKey[1] as number | null;
            return repoId == null || touchedRepos.has(repoId); // "all repos" grid always cares
          },
        });
      }
      if (touchedRuns.size || jobsUnscoped) {
        queryClient.invalidateQueries({
          queryKey: ["jobs"],
          refetchType: "active",
          predicate: (q) => jobsUnscoped || touchedRuns.has(q.queryKey[1] as number),
        });
      }
      touchedRepos.clear();
      touchedRuns.clear();
      runsUnscoped = false;
      jobsUnscoped = false;
    };

    source.addEventListener("update", (e) => {
      try {
        const env = JSON.parse((e as MessageEvent).data) as LiveEnvelope;
        if (env.kind === "run") {
          if (typeof env.data.repo_id === "number") touchedRepos.add(env.data.repo_id);
          else runsUnscoped = true;
        } else if (env.kind === "job") {
          if (typeof env.data.run_id === "number") touchedRuns.add(env.data.run_id);
          else jobsUnscoped = true;
        }
      } catch {
        return; // ignore malformed ticks; the REST read model is the source of truth
      }
      if (flushTimer == null) flushTimer = setTimeout(flush, FLUSH_MS);
    });

    return () => {
      if (flushTimer != null) clearTimeout(flushTimer);
      source.close();
    };
  }, [queryClient]);

  return { connected };
}
