import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { EmptyState, ErrorBanner, ExternalLink, IconShield, TableSkeleton } from "./ui";
import type { AdvisoryImpact } from "../types";

/** Map a GHSA severity onto the shared severity-pill palette. */
const SEV_CLASS: Record<string, string> = {
  critical: "critical",
  high: "high",
  medium: "medium",
  moderate: "medium",
  low: "low",
};

export function AdvisoriesTab() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["advisories"],
    queryFn: api.advisories,
  });
  const entries = data?.entries ?? [];

  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Advisory watch</h2>
          <div className="sub">
            GitHub security advisories (GHSA) joined against the actions your fleet uses —
            most-severe first.
          </div>
        </div>
        {data && (
          <span className="subtle mono" style={{ fontSize: 11 }}>
            {data.advisories_known} advisories · {data.workflows_scanned} workflows scanned
          </span>
        )}
      </div>

      {isError ? (
        <ErrorBanner error={error} />
      ) : isLoading ? (
        <TableSkeleton />
      ) : entries.length === 0 ? (
        <EmptyState icon={<IconShield size={34} />} title="No advisories affect the fleet">
          {data && data.advisories_known === 0
            ? "The advisory inventory hasn't been populated yet — the daily watch sweep refreshes it from the GitHub Advisory Database."
            : "No workflow in the fleet uses an action named in a known security advisory."}
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>Action</th>
                <th>Advisory</th>
                <th>Fixed in</th>
                <th>Impact</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e: AdvisoryImpact) => (
                <tr className="row" key={`${e.ghsa_id}-${e.package}`}>
                  <td>
                    <span className={`sev ${SEV_CLASS[e.severity] ?? "info"}`}>{e.severity}</span>
                  </td>
                  <td className="mono" style={{ color: "var(--fg)" }}>
                    {e.package}
                    {e.vulnerable_range && (
                      <div className="subtle mono" style={{ fontSize: 10, marginTop: 3 }}>
                        {e.vulnerable_range}
                      </div>
                    )}
                  </td>
                  <td>
                    <div className="muted" style={{ maxWidth: 380 }}>
                      {e.summary || e.ghsa_id}
                    </div>
                    <div className="subtle" style={{ fontSize: 11 }}>
                      <ExternalLink href={e.url}>{e.ghsa_id}</ExternalLink>
                    </div>
                  </td>
                  <td className="mono muted">{e.first_patched ?? "—"}</td>
                  <td className="muted">
                    {e.workflows} workflow{e.workflows === 1 ? "" : "s"} · {e.repos} repo
                    {e.repos === 1 ? "" : "s"}
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
