import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { IconCheck, IconRefresh } from "./ui";

/**
 * Re-run a workflow on GitHub via the backend. Compact icon by default; `withLabel` renders the
 * full button used in the run drawer. Errors (e.g. App not configured, missing actions:write
 * scope) surface in the tooltip and turn the control red.
 */
export function RerunButton({ runId, withLabel = false }: { runId: number; withLabel?: boolean }) {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () => api.rerun(runId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }),
  });

  const title = m.isError
    ? `Re-run failed: ${(m.error as Error).message}`
    : "Re-run this workflow on GitHub (needs the App + actions:write)";
  const label = m.isPending ? "Re-running…" : m.isSuccess ? "Requested" : "Re-run";

  return (
    <button
      className={`${withLabel ? "btn sm" : "icon-btn"}${m.isError ? " fail" : ""}`}
      style={m.isError ? { color: "var(--fail)", borderColor: "rgba(248,81,73,0.3)" } : undefined}
      onClick={(e) => {
        e.stopPropagation();
        m.mutate();
      }}
      disabled={m.isPending}
      title={title}
      aria-label="Re-run workflow"
    >
      {m.isSuccess ? <IconCheck /> : <IconRefresh className={m.isPending ? "spin" : ""} />}
      {withLabel && label}
    </button>
  );
}
