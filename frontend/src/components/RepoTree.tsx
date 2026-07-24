import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useRepos } from "../hooks/useRepos";
import { relativeTime } from "../lib/format";
import { IconChevron, IconRepo, IconWorkflow } from "./ui";
import type { Repo, Run, Workflow } from "../types";

export type WorkflowSel = { id: number; name: string } | null;

/** Display name for a workflow: its declared name, else the file basename without extension. */
function workflowLabel(w: Workflow): string {
  if (w.name && w.name.trim()) return w.name;
  const base = w.path.split("/").pop() ?? w.path;
  return base.replace(/\.ya?ml$/i, "");
}

/** Map a run to a status-dot class. */
function runDotClass(run: Run | undefined): string {
  if (!run) return "";
  if (run.status !== "completed") return "running";
  if (run.conclusion === "success") return "ok";
  if (run.conclusion === "failure") return "fail";
  return "";
}

function RepoNode({
  repo,
  active,
  selectedWorkflow,
  onSelectRepo,
  onSelectWorkflow,
}: {
  repo: Repo;
  active: boolean;
  selectedWorkflow: WorkflowSel;
  onSelectRepo: (id: number) => void;
  onSelectWorkflow: (repoId: number, w: WorkflowSel) => void;
}) {
  const [open, setOpen] = useState(false);

  const { data: workflows = [] } = useQuery({
    queryKey: ["workflows", repo.id],
    queryFn: () => api.workflows(repo.id),
    staleTime: 5 * 60_000,
  });

  // Latest run per workflow — only fetched once the repo is expanded.
  const { data: runs = [] } = useQuery({
    queryKey: ["runs", repo.id, ""],
    queryFn: () => api.runs({ repo_id: repo.id }),
    enabled: open,
    staleTime: 60_000,
  });
  const latestByWorkflow = new Map<number, Run>();
  for (const run of runs) {
    if (run.workflow_id == null) continue;
    const prev = latestByWorkflow.get(run.workflow_id);
    if (!prev || (run.run_number ?? 0) > (prev.run_number ?? 0)) latestByWorkflow.set(run.workflow_id, run);
  }

  function toggle() {
    onSelectRepo(repo.id);
    setOpen((o) => !o);
  }

  return (
    <div>
      <button
        className={active ? "repo-row active" : "repo-row"}
        onClick={toggle}
        title={`${repo.owner}/${repo.name}`}
        aria-expanded={open}
      >
        <IconChevron className={open ? "repo-chevron open" : "repo-chevron"} />
        <IconRepo className="repo-icon" />
        <span className="repo-name">
          <span className="repo-owner">{repo.owner}/</span>
          {repo.name}
        </span>
        {workflows.length > 0 && <span className="repo-badge">{workflows.length}</span>}
      </button>

      {open && (
        <ul className="wf-list">
          {workflows.length === 0 ? (
            <li className="wf-empty">no workflows indexed</li>
          ) : (
            workflows.map((w) => {
              const latest = latestByWorkflow.get(w.id);
              const isSel = selectedWorkflow?.id === w.id;
              return (
                <li key={w.id}>
                  <button
                    className={isSel ? "wf-row active" : "wf-row"}
                    onClick={() => onSelectWorkflow(repo.id, { id: w.id, name: workflowLabel(w) })}
                    title={w.path}
                  >
                    <span className={`wf-dot ${runDotClass(latest)}`} />
                    <IconWorkflow className="repo-icon" />
                    <span className="wf-name">{workflowLabel(w)}</span>
                    {latest && <span className="wf-time">{relativeTime(latest.created_at)}</span>}
                  </button>
                </li>
              );
            })
          )}
        </ul>
      )}
    </div>
  );
}

export function RepoTree({
  selectedRepoId,
  selectedWorkflow,
  onSelectRepo,
  onSelectWorkflow,
}: {
  selectedRepoId: number | null;
  selectedWorkflow: WorkflowSel;
  onSelectRepo: (id: number | null) => void;
  onSelectWorkflow: (repoId: number, w: WorkflowSel) => void;
}) {
  const { repos, isLoading } = useRepos();

  return (
    <aside className="sidebar">
      <div className="sidebar-title">Repositories</div>

      <div className="tree">
        <button
          className={selectedRepoId === null && !selectedWorkflow ? "repo-row all active" : "repo-row all"}
          onClick={() => onSelectRepo(null)}
        >
          <IconRepo className="repo-icon" />
          <span className="repo-name">All repositories</span>
        </button>

        {isLoading && <div className="skeleton skeleton-row" style={{ width: "80%" }} />}

        {!isLoading && repos.length === 0 && (
          <p className="muted" style={{ padding: "8px 10px", fontSize: 12 }}>
            No repositories yet. Install the GitHub App (or run the seed script).
          </p>
        )}

        {repos.map((r) => (
          <RepoNode
            key={r.id}
            repo={r}
            active={selectedRepoId === r.id}
            selectedWorkflow={selectedWorkflow}
            onSelectRepo={(id) => onSelectRepo(id)}
            onSelectWorkflow={onSelectWorkflow}
          />
        ))}
      </div>
    </aside>
  );
}
