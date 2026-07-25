import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useHasOperateToken } from "../lib/auth";
import { useRepos } from "../hooks/useRepos";
import { relativeTime } from "../lib/format";
import { operationLabel } from "../lib/labels";
import {
  EmptyState,
  ErrorBanner,
  ExternalLink,
  IconArrowRight,
  IconCheck,
  IconExternal,
  IconLayers,
  IconRefresh,
  IconX,
  TableSkeleton,
} from "./ui";
import type { Campaign, CampaignTarget } from "../types";

/** Map a campaign/target status onto an existing badge palette class. */
function statusClass(status: string): string {
  if (status === "applied" || status === "dry-run-ok" || status === "pr-opened") return "ok";
  if (status === "error" || status === "failed") return "fail";
  if (status === "pending" || status === "dry-run") return "neutral";
  return "running";
}

/** Human-readable label for a raw campaign/target status string (no more "dry-run-ok"). */
const STATUS_LABEL: Record<string, string> = {
  "dry-run-ok": "Ready",
  "dry-run": "Previewing",
  "pr-opened": "PR opened",
  applied: "Applied",
  pending: "Pending",
  error: "Error",
  failed: "Failed",
};
function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status.replace(/-/g, " ");
}

/** Render a unified diff string with per-line +/-/@ coloring (reuses the drift diff styles). */
function DiffBlock({ text }: { text: string }) {
  const lines = text.replace(/\n$/, "").split("\n");
  return (
    <div className="diff">
      {lines.map((line, i) => {
        const head = line[0];
        const t =
          head === "+" ? "add" : head === "-" ? "del" : head === "@" ? "meta" : "ctx";
        return (
          <div key={i} className={`diff-line ${t}`}>
            <span className="diff-text">{line || " "}</span>
          </div>
        );
      })}
    </div>
  );
}

function TargetRow({ target, repoName }: { target: CampaignTarget; repoName: string }) {
  const [open, setOpen] = useState(false);
  const hasDiff = !!target.diff_preview;
  return (
    <div className="campaign-target">
      <button
        className="ct-head"
        onClick={() => hasDiff && setOpen((o) => !o)}
        style={{ cursor: hasDiff ? "pointer" : "default" }}
        aria-expanded={hasDiff ? open : undefined}
      >
        <span className="mono ct-repo">{repoName}</span>
        <span className="ct-right">
          {target.pr_url ? (
            <ExternalLink href={target.pr_url}>
              PR #{target.pr_number} <IconExternal />
            </ExternalLink>
          ) : target.error ? (
            <span className="ct-err mono" title={target.error}>
              {target.error.slice(0, 60)}
            </span>
          ) : hasDiff ? (
            <span className="ct-ok mono">{open ? "hide diff" : "view diff"}</span>
          ) : (
            <span className="subtle mono">no change</span>
          )}
          <span className={`badge ${statusClass(target.status)}`}>{statusLabel(target.status)}</span>
        </span>
      </button>
      {open && hasDiff && <DiffBlock text={target.diff_preview!} />}
    </div>
  );
}

/** Verified-closed panel: did the merged PRs actually resolve the findings the campaign targeted?
 * Re-verify re-audits the target repos so the count reflects the current fleet state. */
function VerifiedClosed({
  v,
  onVerify,
  verifying,
  canVerify,
  error,
}: {
  v: NonNullable<Campaign["verification"]>;
  onVerify: () => void;
  verifying: boolean;
  canVerify: boolean;
  error: string | null;
}) {
  const pct = v.targeted > 0 ? Math.round((v.resolved / v.targeted) * 100) : 0;
  const done = v.targeted > 0 && v.resolved === v.targeted;
  return (
    <div className={`card verified-panel${done ? " done" : ""}`}>
      <div className="verified-head">
        <div>
          <div className="verified-title">
            Verified closed{" "}
            <span className="verified-count mono">
              {v.resolved}/{v.targeted}
            </span>
          </div>
          <div className="verified-sub">
            findings this campaign targeted that a re-audit confirms are now resolved
          </div>
        </div>
        <button
          className="btn sm"
          onClick={onVerify}
          disabled={verifying || !canVerify}
          title={canVerify ? "Re-audit the target repos and refresh the count" : "Set an operate token first"}
        >
          <IconRefresh size={13} /> {verifying ? "Re-auditing…" : "Re-verify"}
        </button>
      </div>
      <div className="verified-bar">
        <span style={{ width: `${pct}%` }} className={done ? "done" : ""} />
      </div>
      {error && (
        <div className="toast err" style={{ marginTop: 8 }}>
          <IconX /> {error}
        </div>
      )}
      {v.by_repo.length > 1 && (
        <div className="verified-repos">
          {v.by_repo
            .slice()
            .sort((a, b) => b.targeted - a.targeted)
            .map((r) => (
              <div className="verified-repo" key={r.repo_id}>
                <span className="mono muted">{r.repo ?? `#${r.repo_id}`}</span>
                <span className={`mono ${r.resolved === r.targeted ? "ct-ok" : "subtle"}`}>
                  {r.resolved}/{r.targeted}
                </span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

function CampaignDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { byId } = useRepos();
  const qc = useQueryClient();
  const hasToken = useHasOperateToken();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["campaign", id],
    queryFn: () => api.campaign(id),
  });

  const apply = useMutation({
    mutationFn: () => api.applyCampaign(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaign", id] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      for (const k of ["findings", "scorecard", "drift"]) qc.invalidateQueries({ queryKey: [k] });
    },
  });

  const verify = useMutation({
    mutationFn: () => api.verifyCampaign(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaign", id] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      for (const k of ["findings", "scorecard"]) qc.invalidateQueries({ queryKey: [k] });
    },
  });

  const targets = data?.targets ?? [];
  const ready = targets.filter((t) => t.status === "dry-run-ok" && t.diff_preview && !t.pr_url);
  const opened = targets.filter((t) => t.pr_url);

  return (
    <section className="drift-page">
      <div className="content-head">
        <div style={{ display: "flex", alignItems: "center", gap: 14, minWidth: 0 }}>
          <button className="btn sm" onClick={onClose} title="Back to campaigns">
            <IconArrowRight className="flip" /> Back
          </button>
          <div style={{ minWidth: 0 }}>
            <h2>{data?.name ?? "Campaign"}</h2>
            <div className="sub">
              {data
                ? `${operationLabel(data.operation)} · ${targets.length} target${targets.length === 1 ? "" : "s"}`
                : "loading…"}
            </div>
          </div>
        </div>
        {data && (
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span className="subtle mono" style={{ fontSize: 11 }}>
              {relativeTime(data.created_at)}
            </span>
            <span className={`badge ${statusClass(data.status)}`}>{statusLabel(data.status)}</span>
          </div>
        )}
      </div>

      {isError ? (
        <ErrorBanner error={error} />
      ) : isLoading || !data ? (
        <TableSkeleton rows={6} />
      ) : (
        <>
          <div className="campaign-detail-bar">
            <span className="muted">
              {ready.length} ready · {opened.length} PR{opened.length === 1 ? "" : "s"} opened
            </span>
            {ready.length > 0 && (
              <button
                className="btn sm primary"
                disabled={!hasToken || apply.isPending}
                onClick={() => apply.mutate()}
                title={hasToken ? `Open ${ready.length} PR(s)` : "Set an operate token first"}
              >
                <IconArrowRight /> {apply.isPending ? "Opening PRs…" : `Open ${ready.length} PR${ready.length === 1 ? "" : "s"}`}
              </button>
            )}
          </div>
          {apply.isError && (
            <div className="toast err">
              <IconX /> {(apply.error as Error).message}
            </div>
          )}
          {apply.data && (
            <div className="toast ok">
              <IconCheck /> Opened {apply.data.targets.filter((t) => t.pr_url).length} PR(s).
            </div>
          )}

          {data.verification && (
            <VerifiedClosed
              v={data.verification}
              onVerify={() => verify.mutate()}
              verifying={verify.isPending}
              canVerify={hasToken}
              error={verify.isError ? (verify.error as Error).message : null}
            />
          )}

          <div className="campaign-target-list">
            {targets.map((t) => {
              const repo = byId.get(t.repo_id);
              const nm = repo ? `${repo.owner}/${repo.name}` : `#${t.repo_id}`;
              return <TargetRow key={t.id} target={t} repoName={nm} />;
            })}
          </div>
        </>
      )}
    </section>
  );
}

export function CampaignsTab() {
  const [selected, setSelected] = useState<number | null>(null);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["campaigns"],
    queryFn: api.campaigns,
  });

  if (selected != null) {
    return <CampaignDetail id={selected} onClose={() => setSelected(null)} />;
  }

  const campaigns = data ?? [];

  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Campaigns</h2>
          <div className="sub">
            Fleet-wide edit waves — each computes per-repo dry-run diffs, then opens reviewed PRs on
            confirm. Launch one from the Policy simulator, Deprecation Radar, or a security finding.
          </div>
        </div>
        {data && (
          <span className="subtle mono" style={{ fontSize: 11 }}>
            {campaigns.length} campaign{campaigns.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {isError ? (
        <ErrorBanner error={error} />
      ) : isLoading ? (
        <TableSkeleton />
      ) : campaigns.length === 0 ? (
        <EmptyState icon={<IconLayers size={34} />} title="No campaigns yet">
          Create a fix campaign from the Policy, Radar, or Security tab — it will appear here with its
          per-repo diffs and PR status.
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Operation</th>
                <th>Targets</th>
                <th>Verified closed</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c) => (
                <tr className="row clickable" key={c.id} onClick={() => setSelected(c.id)}>
                  <td>
                    <span style={{ color: "var(--fg)", fontWeight: 500 }}>{c.name}</span>
                  </td>
                  <td className="muted" title={c.operation}>{operationLabel(c.operation)}</td>
                  <td className="muted">
                    {c.targets} repo{c.targets === 1 ? "" : "s"}
                    {c.opened > 0 && <span className="ct-ok mono"> · {c.opened} PR</span>}
                    {c.errored > 0 && <span className="ct-err mono"> · {c.errored} err</span>}
                  </td>
                  <td>
                    {c.targeted_findings > 0 ? (
                      <span
                        className={`badge ${c.resolved_findings === c.targeted_findings ? "ok" : "neutral"}`}
                        title="Findings resolved / targeted (verified by re-audit)"
                      >
                        {c.resolved_findings}/{c.targeted_findings} closed
                      </span>
                    ) : (
                      <span className="subtle mono">—</span>
                    )}
                  </td>
                  <td>
                    <span className={`badge ${statusClass(c.status)}`}>{statusLabel(c.status)}</span>
                  </td>
                  <td className="muted">{relativeTime(c.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
