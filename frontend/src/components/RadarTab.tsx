import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { CampaignButton } from "./CampaignButton";
import { EmptyState, ErrorBanner, ExternalLink, IconLayers, TableSkeleton } from "./ui";
import type { RadarEntry } from "../types";

const STATUS_CLASS: Record<string, string> = {
  overdue: "critical",
  urgent: "high",
  upcoming: "low",
  "no-date": "info",
};

/** Build the W3 op params from a radar entry's target/replacement. */
function paramsFor(e: RadarEntry): Record<string, unknown> | null {
  if (e.fix_operation === "swap-runs-on") return { from: e.target, to: e.replacement };
  if (e.fix_operation === "bump-action-version") {
    return { action: e.target.split("@")[0], to: e.replacement.split("@")[1] ?? e.replacement };
  }
  return null;
}

function countdown(days: number | null): string {
  if (days == null) return "—";
  if (days < 0) return `${Math.abs(days)}d overdue`;
  if (days === 0) return "today";
  return `${days}d left`;
}

export function RadarTab() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["radar"],
    queryFn: api.radar,
  });
  const entries = data?.entries ?? [];

  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Deprecation radar</h2>
          <div className="sub">
            Dated GitHub deprecations joined against your fleet — deadline-sorted, with one-click
            migrations.
          </div>
        </div>
        {data && (
          <span className="subtle mono" style={{ fontSize: 11 }}>
            {data.workflows_scanned} workflows scanned
          </span>
        )}
      </div>

      {isError ? (
        <ErrorBanner error={error} />
      ) : isLoading ? (
        <TableSkeleton />
      ) : entries.length === 0 ? (
        <EmptyState icon={<IconLayers size={34} />} title="Nothing deprecated in range">
          No workflow in the fleet hits a known runner-image or action-version deprecation.
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Deadline</th>
                <th>Deprecation</th>
                <th>Replacement</th>
                <th>Impact</th>
                <th aria-label="fix" />
              </tr>
            </thead>
            <tbody>
              {entries.map((e: RadarEntry) => (
                <tr className="row" key={e.id}>
                  <td>
                    <span className={`sev ${STATUS_CLASS[e.status] ?? "info"}`}>
                      {countdown(e.days_until)}
                    </span>
                    <div className="subtle mono" style={{ fontSize: 10, marginTop: 3 }}>
                      {e.deadline ?? "no date"}
                    </div>
                  </td>
                  <td>
                    <span className="mono" style={{ color: "var(--fg)" }}>
                      {e.target}
                    </span>
                    <div className="subtle" style={{ fontSize: 11 }}>
                      <ExternalLink href={e.reference}>changelog</ExternalLink>
                    </div>
                  </td>
                  <td className="mono muted">{e.replacement}</td>
                  <td className="muted">
                    {e.workflows} workflow{e.workflows === 1 ? "" : "s"} · {e.repos} repo
                    {e.repos === 1 ? "" : "s"}
                  </td>
                  <td className="actions">
                    {e.fix_operation ? (
                      <CampaignButton
                        name={`Migrate ${e.target} → ${e.replacement}`}
                        operation={e.fix_operation}
                        repoIds={e.fixable_repo_ids}
                        params={paramsFor(e)}
                        label="Fix"
                      />
                    ) : (
                      <span className="subtle" style={{ fontSize: 12 }}>
                        manual
                      </span>
                    )}
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
