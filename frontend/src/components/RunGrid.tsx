import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useRepos } from "../hooks/useRepos";
import { commitUrl, runUrl } from "../lib/github";
import { duration, relativeTime, shortSha } from "../lib/format";
import {
  EmptyState,
  ErrorBanner,
  ExternalLink,
  IconActivity,
  IconBranch,
  IconExternal,
  StatusBadge,
  TableSkeleton,
} from "./ui";
import { RerunButton } from "./RerunButton";
import type { Run } from "../types";

const STATUS_OPTIONS: [string, string][] = [
  ["", "Any status"],
  ["queued", "Queued"],
  ["in_progress", "In progress"],
  ["completed", "Completed"],
];

export function RunGrid({
  repoId,
  onSelect,
  selectedRunId,
}: {
  repoId: number | null;
  onSelect: (run: Run) => void;
  selectedRunId: number | null;
}) {
  const [status, setStatus] = useState("");
  const { byId } = useRepos();
  const { data: runs = [], isLoading, isError, error } = useQuery({
    queryKey: ["runs", repoId, status],
    queryFn: () => api.runs({ repo_id: repoId ?? undefined, status: status || undefined }),
    // SSE (useEventStream) drives freshness; keep a 5-min staleTime as a fallback if the stream
    // drops, instead of a blanket 30s poll per grid.
    staleTime: 5 * 60_000,
  });

  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Workflow runs</h2>
          <div className="sub">
            Live history across {repoId ? "this repository" : "all watched repositories"}
          </div>
        </div>
        <label className="field">
          Status
          <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_OPTIONS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
      </div>

      {isError ? (
        <ErrorBanner error={error} />
      ) : isLoading ? (
        <TableSkeleton />
      ) : runs.length === 0 ? (
        <EmptyState icon={<IconActivity size={34} />} title="No runs yet">
          Install the GitHub App and trigger a workflow, or run{" "}
          <span className="mono">scripts/seed_local.py</span> to load demo data.
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Branch</th>
                <th>Commit</th>
                <th>Event</th>
                <th>Status</th>
                <th>Triggered</th>
                <th>Duration</th>
                <th aria-label="actions" />
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const repo = byId.get(run.repo_id);
                return (
                  <tr
                    key={run.id}
                    className={`row clickable${selectedRunId === run.id ? " selected" : ""}`}
                    onClick={() => onSelect(run)}
                  >
                    <td>
                      <strong>#{run.run_number}</strong>
                      {repoId === null && repo && (
                        <div className="subtle mono" style={{ fontSize: 11 }}>
                          {repo.owner}/{repo.name}
                        </div>
                      )}
                    </td>
                    <td>
                      <span className="chip">
                        <IconBranch /> {run.head_branch ?? "—"}
                      </span>
                    </td>
                    <td>
                      {repo && run.head_sha ? (
                        <a
                          className="chip mono"
                          href={commitUrl(repo, run.head_sha)}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {shortSha(run.head_sha)}
                        </a>
                      ) : (
                        <span className="chip mono">{shortSha(run.head_sha)}</span>
                      )}
                    </td>
                    <td className="muted">{run.event ?? "—"}</td>
                    <td>
                      <StatusBadge run={run} />
                    </td>
                    <td className="muted" title={run.created_at ?? ""}>
                      {relativeTime(run.created_at)}
                      {run.actor && <span className="subtle"> · {run.actor}</span>}
                    </td>
                    <td className="muted">{duration(run.started_at, run.completed_at)}</td>
                    <td className="actions" onClick={(e) => e.stopPropagation()}>
                      {repo && (
                        <ExternalLink href={runUrl(repo, run)}>
                          <span className="icon-btn" title="View run on GitHub">
                            <IconExternal />
                          </span>
                        </ExternalLink>
                      )}
                      {run.status === "completed" && <RerunButton runId={run.id} />}
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
