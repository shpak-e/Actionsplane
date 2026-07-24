import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { LiveEnvelope } from "../types";

/**
 * Subscribe to the backend SSE stream and invalidate the relevant React Query caches when a
 * live run/job update arrives — sub-second dashboard refresh without polling (plan §5.1).
 *
 * The browser's EventSource auto-reconnects on transient drops, but it gives up quietly if the
 * network is down for a while or a proxy closes an idle connection, leaving the indicator stuck on
 * "Reconnecting…". So we (a) expose a `reconnect()` the UI can call from a button, and (b) force a
 * fresh connection whenever the tab becomes visible again or the browser reports it's back online —
 * the two moments a stale stream is most likely and a user is most likely watching.
 */
export function useEventStream(): { connected: boolean; reconnect: () => void } {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [nonce, setNonce] = useState(0);
  const connectedRef = useRef(false);

  const reconnect = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const source = new EventSource("/api/v1/events/stream");
    source.onopen = () => {
      connectedRef.current = true;
      setConnected(true);
    };
    source.onerror = () => {
      connectedRef.current = false;
      setConnected(false);
    };

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

    // Kick a reconnect when the tab is refocused or the network returns and the stream is down.
    const revive = () => {
      if (!connectedRef.current && (document.visibilityState === "visible" || navigator.onLine)) {
        reconnect();
      }
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") revive();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", revive);
    window.addEventListener("focus", revive);

    return () => {
      if (flushTimer != null) clearTimeout(flushTimer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", revive);
      window.removeEventListener("focus", revive);
      source.close();
    };
  }, [queryClient, nonce, reconnect]);

  return { connected, reconnect };
}
