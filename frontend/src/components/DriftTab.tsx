import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useRepos } from "../hooks/useRepos";
import { relativeTime } from "../lib/format";
import { DriftPill, EmptyState, ErrorBanner, IconLayers, TableSkeleton } from "./ui";
import { DriftDetail } from "./DriftDetail";
import type { Binding } from "../types";

export function DriftTab({ repoId }: { repoId: number | null }) {
  const { byId } = useRepos();
  const [openBinding, setOpenBinding] = useState<number | null>(null);
  const { data: bindings = [], isLoading, isError, error } = useQuery({
    queryKey: ["drift", repoId],
    queryFn: () => api.drift(repoId ?? undefined),
  });

  // A selected binding takes over the whole tab as a full-page diff view.
  if (openBinding != null) {
    return <DriftDetail bindingId={openBinding} onClose={() => setOpenBinding(null)} />;
  }

  const drifted = bindings.filter(
    (b) => b.drift_severity && !["identical", "minor"].includes(b.drift_severity),
  ).length;

  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Workflow drift</h2>
          <div className="sub">
            {bindings.length} binding{bindings.length === 1 ? "" : "s"}
            {drifted > 0 && ` · ${drifted} drifting from template`}
          </div>
        </div>
      </div>

      {isError ? (
        <ErrorBanner error={error} />
      ) : isLoading ? (
        <TableSkeleton />
      ) : bindings.length === 0 ? (
        <EmptyState icon={<IconLayers size={34} />} title="No template bindings">
          Define a canonical template and the drift sweep will bind matching workflows and score
          their divergence automatically.
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Repository</th>
                <th>Workflow</th>
                <th>Template</th>
                <th>Drift</th>
                <th>Last checked</th>
                <th aria-label="view" />
              </tr>
            </thead>
            <tbody>
              {bindings.map((b: Binding) => {
                const repo = byId.get(b.repo_id);
                return (
                  <tr
                    className="row clickable"
                    key={b.id}
                    onClick={() => setOpenBinding(b.id)}
                    title="View what drifted"
                  >
                    <td className="muted">
                      {repo ? `${repo.owner}/${repo.name}` : `#${b.repo_id}`}
                    </td>
                    <td className="mono">{b.path}</td>
                    <td className="muted">#{b.template_id}</td>
                    <td>
                      <DriftPill severity={b.drift_severity} />
                    </td>
                    <td className="muted">{relativeTime(b.last_drift_check_at)}</td>
                    <td className="actions">
                      <span className="btn sm">View diff</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
