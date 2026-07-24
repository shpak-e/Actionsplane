import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { EmptyState, ErrorBanner, IconShield, ScoreRing, SeverityPill, TableSkeleton } from "./ui";
import { FindingFix } from "./FindingFix";
import type { Finding } from "../types";

const SEVERITIES: [string, string][] = [
  ["", "All severities"],
  ["critical", "Critical"],
  ["high", "High"],
  ["medium", "Medium"],
  ["low", "Low"],
  ["info", "Info"],
];
const SEV_ORDER = ["critical", "high", "medium", "low", "info"];
const SEV_COLOR: Record<string, string> = {
  critical: "var(--sev-critical)",
  high: "var(--sev-high)",
  medium: "var(--sev-medium)",
  low: "var(--sev-low)",
  info: "var(--sev-info)",
};

export function SecurityTab({ repoId }: { repoId: number | null }) {
  const [severity, setSeverity] = useState("");
  const { data: scorecard } = useQuery({ queryKey: ["scorecard"], queryFn: api.scorecard });
  const { data: findingsPage, isLoading, isError, error } = useQuery({
    queryKey: ["findings", repoId, severity],
    queryFn: () => api.findings({ repo_id: repoId ?? undefined, severity: severity || undefined }),
  });
  const findings = findingsPage?.items ?? [];

  const total = scorecard
    ? Object.values(scorecard.by_severity).reduce((a, b) => a + b, 0)
    : 0;

  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Security posture</h2>
          <div className="sub">
            Supply-chain &amp; hygiene findings across {repoId ? "this repository" : "the org"}
          </div>
        </div>
        <label className="field">
          Severity
          <select className="input" value={severity} onChange={(e) => setSeverity(e.target.value)}>
            {SEVERITIES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
      </div>

      {scorecard && (
        <div className="card scorecard">
          <ScoreRing score={scorecard.score} />
          <div className="scorecard-stats">
            <div className="stat">
              <span className="stat-label">Open findings</span>
              <div className="stat-value">{scorecard.open_findings}</div>
            </div>
            <div className="stat">
              <span className="stat-label">High / Critical</span>
              <div
                className={`stat-value${
                  (scorecard.by_severity.high ?? 0) + (scorecard.by_severity.critical ?? 0) > 0
                    ? " fail"
                    : ""
                }`}
              >
                {(scorecard.by_severity.high ?? 0) + (scorecard.by_severity.critical ?? 0)}
              </div>
            </div>
            <div className="stat">
              <span className="stat-label">Repositories</span>
              <div className="stat-value">{scorecard.repos}</div>
            </div>

            {total > 0 && (
              <>
                <div className="sevbar">
                  {SEV_ORDER.map((s) => {
                    const n = scorecard.by_severity[s] ?? 0;
                    return n > 0 ? (
                      <span
                        key={s}
                        style={{ width: `${(n / total) * 100}%`, background: SEV_COLOR[s] }}
                        title={`${s}: ${n}`}
                      />
                    ) : null;
                  })}
                </div>
                <div className="sevbar-legend">
                  {SEV_ORDER.filter((s) => (scorecard.by_severity[s] ?? 0) > 0).map((s) => (
                    <span key={s}>
                      <span className="dot" style={{ background: SEV_COLOR[s] }} />
                      {s} · {scorecard.by_severity[s]}
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {isError ? (
        <ErrorBanner error={error} />
      ) : isLoading ? (
        <TableSkeleton />
      ) : findings.length === 0 ? (
        <EmptyState icon={<IconShield size={34} />} title="No open findings">
          Clean — or no audit has run yet. The worker audits watched repos on a schedule; local
          seed data includes a few sample findings.
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>Type</th>
                <th>Path</th>
                <th>Reference</th>
                <th>Finding</th>
                <th aria-label="fix" />
              </tr>
            </thead>
            <tbody>
              {findings.map((f: Finding) => (
                <tr className="row" key={f.id}>
                  <td>
                    <SeverityPill severity={f.severity} />
                  </td>
                  <td className="muted">{f.finding_type.replace(/_/g, " ")}</td>
                  <td className="mono">{f.path ?? "—"}</td>
                  <td className="mono">{f.ref ?? "—"}</td>
                  <td>{f.message}</td>
                  <td className="actions">
                    <FindingFix finding={f} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
