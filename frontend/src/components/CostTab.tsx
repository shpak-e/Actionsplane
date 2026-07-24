import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { fmtSecs, relativeTime } from "../lib/format";
import { operationLabel } from "../lib/labels";
import { EmptyState, ErrorBanner, IconActivity, IconChevron, IconZap, TableSkeleton } from "./ui";
import type { FleetCostRow } from "../types";

type SortKey = "total_minutes" | "p95_duration_s" | "p95_queue_s" | "runs";

const SORTS: { key: SortKey; label: string; hint: string }[] = [
  { key: "total_minutes", label: "Most compute", hint: "Total run-minutes (cost proxy)" },
  { key: "p95_duration_s", label: "Slowest", hint: "p95 run duration" },
  { key: "p95_queue_s", label: "Longest queue", hint: "p95 time waiting for a runner" },
  { key: "runs", label: "Most runs", hint: "Run count in the window" },
];

const WINDOWS = [7, 30, 90];

function fmtMin(m: number): string {
  if (m < 60) return `${m.toFixed(0)}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${Math.round(m % 60)}m`;
}

export function CostTab() {
  const [days, setDays] = useState(30);
  const [sort, setSort] = useState<SortKey>("total_minutes");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["fleet-cost", days],
    queryFn: () => api.fleetCost(days),
  });

  const rows = [...(data?.rows ?? [])].sort((a, b) => (b[sort] ?? 0) - (a[sort] ?? 0));
  const max = Math.max(1, ...rows.map((r) => (r[sort] as number) ?? 0));

  // Fleet-wide optimization opportunities: how many workflows have at least one suggestion, and
  // how many carry a warn-level one (failing / queuing) — the headline "there's money on the table".
  const withTips = rows.filter((r) => r.suggestions.length > 0);
  const warnCount = rows.filter((r) => r.suggestions.some((s) => s.severity === "warn")).length;

  function toggle(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Cost &amp; time</h2>
          <div className="sub">
            Where your Actions minutes go — the longest-running and most-expensive workflows across
            the fleet.
          </div>
        </div>
        <div className="cost-controls">
          <div className="seg">
            {WINDOWS.map((w) => (
              <button
                key={w}
                className={days === w ? "seg-btn active" : "seg-btn"}
                onClick={() => setDays(w)}
              >
                {w}d
              </button>
            ))}
          </div>
        </div>
      </div>

      {data && (
        <div className="card cost-summary">
          <div className="dash-metric">
            <div className="dash-metric-val">{fmtMin(data.total_minutes)}</div>
            <div className="dash-metric-label">Total compute · last {data.window_days}d</div>
          </div>
          <div className="dash-metric">
            <div className="dash-metric-val">{rows.length}</div>
            <div className="dash-metric-label">Workflows with runs</div>
          </div>
          <div className="dash-metric">
            <div className="dash-metric-val">
              {withTips.length}
              {warnCount > 0 && <span className="cost-warn-dot" title={`${warnCount} need attention`} />}
            </div>
            <div className="dash-metric-label">With optimization tips</div>
          </div>
          <div className="seg cost-sort">
            {SORTS.map((s) => (
              <button
                key={s.key}
                className={sort === s.key ? "seg-btn active" : "seg-btn"}
                onClick={() => setSort(s.key)}
                title={s.hint}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {isError ? (
        <ErrorBanner error={error} />
      ) : isLoading ? (
        <TableSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState icon={<IconActivity size={34} />} title="No run data in this window">
          Once watched repositories run workflows, their compute cost and durations appear here.
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="cost-tip-col" />
                <th>Workflow</th>
                <th>Repository</th>
                <th className="num">Runs</th>
                <th className="num">Compute</th>
                <th className="num">Avg</th>
                <th className="num">p95</th>
                <th className="num">Queue p95</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r: FleetCostRow) => {
                const barVal = (r[sort] as number) ?? 0;
                const hasTips = r.suggestions.length > 0;
                const isOpen = expanded.has(r.workflow_id);
                const worst = r.suggestions.some((s) => s.severity === "warn") ? "warn" : "info";
                return (
                  <Fragment key={r.workflow_id}>
                    <tr
                      className={hasTips ? "row cost-row has-tips" : "row cost-row"}
                      onClick={hasTips ? () => toggle(r.workflow_id) : undefined}
                    >
                      <td className="cost-tip-col">
                        {hasTips && (
                          <button
                            className={`cost-tip-btn ${worst}`}
                            aria-label={`${r.suggestions.length} optimization tips`}
                            aria-expanded={isOpen}
                            title={`${r.suggestions.length} optimization ${
                              r.suggestions.length === 1 ? "tip" : "tips"
                            }`}
                          >
                            <IconZap size={13} />
                            <IconChevron size={11} className={isOpen ? "rot90" : ""} />
                          </button>
                        )}
                      </td>
                      <td>
                        <span style={{ color: "var(--fg)", fontWeight: 500 }}>{r.name}</span>
                        {r.failures > 0 && (
                          <span className="cost-fail mono" title="Failed runs in the window">
                            {" "}
                            · {r.failures} failed
                          </span>
                        )}
                        <div className="cost-bar">
                          <span style={{ width: `${(barVal / max) * 100}%` }} />
                        </div>
                      </td>
                      <td className="muted mono">{r.repo ?? "—"}</td>
                      <td className="num mono">{r.runs}</td>
                      <td className="num mono cost-total">{fmtMin(r.total_minutes)}</td>
                      <td className="num mono muted">{fmtSecs(r.avg_duration_s)}</td>
                      <td className="num mono muted">{fmtSecs(r.p95_duration_s)}</td>
                      <td className="num mono muted">{fmtSecs(r.p95_queue_s)}</td>
                    </tr>
                    {isOpen && (
                      <tr className="cost-tips-row">
                        <td />
                        <td colSpan={7}>
                          <div className="cost-tips">
                            {r.suggestions.map((s, i) => (
                              <div className={`cost-tip ${s.severity}`} key={i}>
                                <IconZap size={13} className="cost-tip-ico" />
                                <div>
                                  {s.message}
                                  {s.fix_operation && (
                                    <span className="cost-tip-fix">
                                      One-click fix available: {operationLabel(s.fix_operation)} — open
                                      it from the Security tab for this workflow.
                                    </span>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          <p className="subtle" style={{ fontSize: 11.5, marginTop: 8 }}>
            “Compute” is the sum of run durations (a proxy for billed minutes; actual cost depends on
            runner size/OS). The bar reflects the active sort. Last run:{" "}
            {relativeTime(rows[0]?.last_run_at)}.
          </p>
        </div>
      )}
    </section>
  );
}
