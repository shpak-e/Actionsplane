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
    source.addEventListener("update", (e) => {
      try {
        const env = JSON.parse((e as MessageEvent).data) as LiveEnvelope;
        if (env.kind === "run") queryClient.invalidateQueries({ queryKey: ["runs"] });
        if (env.kind === "job") queryClient.invalidateQueries({ queryKey: ["jobs"] });
      } catch {
        /* ignore malformed ticks; the REST read model is the source of truth */
      }
    });
    return () => source.close();
  }, [queryClient]);

  return { connected };
}
