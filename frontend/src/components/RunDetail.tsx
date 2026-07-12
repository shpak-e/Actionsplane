import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useRepos } from "../hooks/useRepos";
import { branchUrl, commitUrl, jobLogUrl, runUrl } from "../lib/github";
import { duration, relativeTime, shortSha } from "../lib/format";
import {
  ExternalLink,
  IconCheck,
  IconExternal,
  IconLogs,
  IconRefresh,
  IconX,
  StatusBadge,
} from "./ui";
import { MetricsPanel } from "./MetricsPanel";
import type { Job, Run, Step } from "../types";

function jobColor(job: Job): string {
  if (job.status !== "completed") return "var(--warn-soft)";
  if (job.conclusion === "success") return "var(--ok-soft)";
  if (job.conclusion === "failure") return "var(--fail-soft)";
  return "var(--fg-subtle)";
}

function stepColor(step: Step): string {
  if (step.conclusion === "success") return "var(--ok-soft)";
  if (step.conclusion === "failure") return "var(--fail-soft)";
  if (step.conclusion === "skipped" || step.conclusion === "cancelled") return "var(--fg-subtle)";
  if (step.status === "in_progress") return "var(--warn-soft)";
  return "var(--border-strong)"; // queued / unknown
}

export function RunDetail({ run, onClose }: { run: Run; onClose: () => void }) {
  const { byId } = useRepos();
  const repo = byId.get(run.repo_id);
  const qc = useQueryClient();
  const { data: jobs = [] } = useQuery({ queryKey: ["jobs", run.id], queryFn: () => api.jobs(run.id) });
  const rerun = useMutation({
    mutationFn: () => api.rerun(run.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={`Run ${run.run_number}`}>
        <div className="drawer-head">
          <div>
            <h2>Run #{run.run_number}</h2>
            {repo && (
              <div className="subtle mono" style={{ fontSize: 12 }}>
                {repo.owner}/{repo.name}
              </div>
            )}
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <IconX size={16} />
          </button>
        </div>

        <div className="drawer-body">
          <div style={{ marginBottom: 4 }}>
            <StatusBadge run={run} />
          </div>

          <dl className="facts" style={{ marginTop: 16 }}>
            <dt>Branch</dt>
            <dd>
              {repo && run.head_branch ? (
                <ExternalLink href={branchUrl(repo, run.head_branch)}>{run.head_branch}</ExternalLink>
              ) : (
                run.head_branch ?? "—"
              )}
            </dd>
            <dt>Commit</dt>
            <dd>
              {repo && run.head_sha ? (
                <ExternalLink href={commitUrl(repo, run.head_sha)}>
                  <span className="mono">{shortSha(run.head_sha)}</span>
                </ExternalLink>
              ) : (
                <span className="mono">{shortSha(run.head_sha)}</span>
              )}
            </dd>
            <dt>Event</dt>
            <dd className="muted">{run.event ?? "—"}</dd>
            <dt>Actor</dt>
            <dd className="muted">{run.actor ?? "—"}</dd>
            <dt>Started</dt>
            <dd className="muted">{relativeTime(run.started_at)}</dd>
            <dt>Duration</dt>
            <dd className="muted">{duration(run.started_at, run.completed_at)}</dd>
          </dl>

          <div className="drawer-actions">
            {repo && (
              <ExternalLink href={runUrl(repo, run)}>
                <span className="btn sm">
                  <IconExternal /> View on GitHub
                </span>
              </ExternalLink>
            )}
            {repo && (
              <ExternalLink href={runUrl(repo, run)}>
                <span className="btn sm">
                  <IconLogs /> Logs
                </span>
              </ExternalLink>
            )}
            {run.status === "completed" && (
              <button
                className="btn sm primary"
                onClick={() => rerun.mutate()}
                disabled={rerun.isPending}
              >
                <IconRefresh className={rerun.isPending ? "spin" : ""} />
                {rerun.isPending ? "Re-running…" : "Re-run"}
              </button>
            )}
          </div>

          {rerun.isSuccess && (
            <div className="toast ok">
              <IconCheck /> Re-run requested on GitHub.
            </div>
          )}
          {rerun.isError && (
            <div className="toast err">
              <IconX /> {(rerun.error as Error).message}
            </div>
          )}

          {run.workflow_id != null && (
            <>
              <div className="drawer-section-title">Workflow metrics</div>
              <MetricsPanel workflowId={run.workflow_id} />
            </>
          )}

          <div className="drawer-section-title">Jobs</div>
          {jobs.length === 0 ? (
            <p className="muted">No jobs recorded for this run.</p>
          ) : (
            <ul className="jobs">
              {jobs.map((j: Job) => (
                <li key={j.id} className="job">
                  <div className="job-row">
                    <span className="job-name">
                      <span
                        style={{
                          width: 8,
                          height: 8,
                          borderRadius: "50%",
                          background: jobColor(j),
                          flex: "0 0 auto",
                        }}
                      />
                      <span>{j.name ?? "job"}</span>
                    </span>
                    <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span className="subtle" style={{ fontSize: 12 }}>
                        {j.conclusion ?? j.status ?? ""}
                      </span>
                      {repo && (
                        <ExternalLink href={jobLogUrl(repo, run, j)}>
                          <span className="icon-btn" title="View job logs on GitHub">
                            <IconLogs />
                          </span>
                        </ExternalLink>
                      )}
                    </span>
                  </div>
                  {j.steps.length > 0 && (
                    <ol className="steps">
                      {j.steps.map((s: Step, i: number) => (
                        <li
                          key={s.number ?? i}
                          className={`step${s.conclusion === "failure" ? " failed" : ""}`}
                        >
                          <span className="step-dot" style={{ background: stepColor(s) }} />
                          <span className="step-name">{s.name ?? "step"}</span>
                          <span className="step-concl">{s.conclusion ?? s.status ?? ""}</span>
                        </li>
                      ))}
                    </ol>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </>
  );
}
