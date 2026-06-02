import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { relativeTime } from "../lib/format";
import { IconRefresh } from "./ui";

/** Offline-mode control: re-pull all configured repos from GitHub, then refresh every view. */
export function SyncButton({ syncedAt }: { syncedAt: string | null }) {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () => api.offlineSync(),
    onSuccess: () => {
      for (const k of ["repos", "runs", "findings", "scorecard", "drift", "mode"]) {
        qc.invalidateQueries({ queryKey: [k] });
      }
    },
  });

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <span className="live">
        <span className="dot" />
        Offline
      </span>
      <span className="subtle" style={{ fontSize: 12 }}>
        {m.isError
          ? "sync failed"
          : syncedAt
            ? `synced ${relativeTime(syncedAt)}`
            : "not synced yet"}
      </span>
      <button
        className="btn sm"
        onClick={() => m.mutate()}
        disabled={m.isPending}
        title="Re-fetch all offline repos from GitHub"
      >
        <IconRefresh className={m.isPending ? "spin" : ""} />
        {m.isPending ? "Syncing…" : "Sync"}
      </button>
    </div>
  );
}
