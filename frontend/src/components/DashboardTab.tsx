import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { operationLabel } from "../lib/labels";
import { useDashboardLayout, type WidgetId } from "../hooks/useDashboardLayout";
import { IconArrowRight, IconChevron, IconLayers, ScoreRing, StatusBadge } from "./ui";

const SEV_ORDER = ["critical", "high", "medium", "low", "info"];
const SEV_COLOR: Record<string, string> = {
  critical: "var(--sev-critical)",
  high: "var(--sev-high)",
  medium: "var(--sev-medium)",
  low: "var(--sev-low)",
  info: "var(--sev-info)",
};

const WIDGET_META: Record<WidgetId, { title: string; tab: string }> = {
  posture: { title: "Security posture", tab: "security" },
  severity: { title: "Findings by severity", tab: "security" },
  drift: { title: "Workflow drift", tab: "drift" },
  radar: { title: "Deprecation deadlines", tab: "radar" },
  runs: { title: "Recent runs", tab: "runs" },
  campaigns: { title: "Campaigns", tab: "campaigns" },
  pipelines: { title: "Pipelines", tab: "pipelines" },
  fleet: { title: "Fleet", tab: "repos" },
};

function Metric({ value, label, tone }: { value: React.ReactNode; label: string; tone?: string }) {
  return (
    <div className="dash-metric">
      <div className={`dash-metric-val${tone ? ` ${tone}` : ""}`}>{value}</div>
      <div className="dash-metric-label">{label}</div>
    </div>
  );
}

export function DashboardTab({ onNavigate }: { onNavigate: (tab: string) => void }) {
  const { layout, isVisible, toggle, move, reset } = useDashboardLayout();
  const [customizing, setCustomizing] = useState(false);

  const scorecard = useQuery({ queryKey: ["scorecard"], queryFn: api.scorecard });
  const drift = useQuery({ queryKey: ["drift", null], queryFn: () => api.drift() });
  const radar = useQuery({ queryKey: ["radar"], queryFn: api.radar });
  const runs = useQuery({ queryKey: ["runs", null, null, ""], queryFn: () => api.runs({}) });
  const campaigns = useQuery({ queryKey: ["campaigns"], queryFn: api.campaigns });
  const pipelines = useQuery({ queryKey: ["pipelines"], queryFn: api.pipelines });
  const repos = useQuery({ queryKey: ["repos"], queryFn: api.repos });

  const driftingCount = (drift.data ?? []).filter(
    (b) => b.drift_severity && !["identical", "minor"].includes(b.drift_severity),
  ).length;
  const urgentRadar = (radar.data?.entries ?? []).filter(
    (e) => e.status === "overdue" || e.status === "urgent",
  );

  function renderWidget(id: WidgetId): React.ReactNode {
    switch (id) {
      case "posture":
        return scorecard.data ? (
          <div className="dash-posture">
            <ScoreRing score={scorecard.data.score} />
            <div className="dash-metric-row">
              <Metric value={scorecard.data.open_findings} label="Open findings" />
              <Metric
                value={(scorecard.data.by_severity.critical ?? 0) + (scorecard.data.by_severity.high ?? 0)}
                label="High / Critical"
                tone={
                  (scorecard.data.by_severity.critical ?? 0) + (scorecard.data.by_severity.high ?? 0) > 0
                    ? "fail"
                    : undefined
                }
              />
              <Metric value={scorecard.data.repos} label="Repositories" />
            </div>
          </div>
        ) : (
          <div className="dash-empty">—</div>
        );
      case "severity": {
        const by = scorecard.data?.by_severity ?? {};
        const total = SEV_ORDER.reduce((a, s) => a + (by[s] ?? 0), 0);
        return total === 0 ? (
          <div className="dash-empty">No open findings.</div>
        ) : (
          <ul className="dash-list">
            {SEV_ORDER.filter((s) => (by[s] ?? 0) > 0).map((s) => (
              <li key={s}>
                <span>
                  <span className="dash-dot" style={{ background: SEV_COLOR[s] }} /> {s}
                </span>
                <span className="mono">{by[s]}</span>
              </li>
            ))}
          </ul>
        );
      }
      case "drift":
        return (
          <div className="dash-metric-row">
            <Metric value={driftingCount} label="Drifting" tone={driftingCount > 0 ? "fail" : undefined} />
            <Metric value={drift.data?.length ?? 0} label="Bindings tracked" />
          </div>
        );
      case "radar":
        return urgentRadar.length === 0 ? (
          <div className="dash-empty">Nothing urgent — no deprecation deadline within 90 days.</div>
        ) : (
          <ul className="dash-list">
            {urgentRadar.slice(0, 4).map((e) => (
              <li key={e.id}>
                <span className="mono dash-ellipsis">{e.target}</span>
                <span className={`sev ${e.status === "overdue" ? "critical" : "high"}`}>
                  {e.days_until != null && e.days_until < 0
                    ? `${Math.abs(e.days_until)}d overdue`
                    : `${e.days_until}d left`}
                </span>
              </li>
            ))}
          </ul>
        );
      case "runs":
        return (runs.data ?? []).length === 0 ? (
          <div className="dash-empty">No runs recorded yet.</div>
        ) : (
          <ul className="dash-list">
            {(runs.data ?? []).slice(0, 5).map((r) => (
              <li key={r.id}>
                <span className="dash-ellipsis mono">
                  {r.head_branch ?? "—"} #{r.run_number}
                </span>
                <StatusBadge run={r} />
              </li>
            ))}
          </ul>
        );
      case "campaigns":
        return (campaigns.data ?? []).length === 0 ? (
          <div className="dash-empty">No campaigns yet.</div>
        ) : (
          <ul className="dash-list">
            {(campaigns.data ?? []).slice(0, 4).map((c) => (
              <li key={c.id}>
                <span className="dash-ellipsis">
                  {c.name}
                  <span className="subtle mono"> · {operationLabel(c.operation)}</span>
                </span>
                <span className="mono subtle">
                  {c.opened > 0 ? `${c.opened} PR` : `${c.targets} repo`}
                </span>
              </li>
            ))}
          </ul>
        );
      case "pipelines":
        return (
          <div className="dash-metric-row">
            <Metric value={pipelines.data?.pipelines.length ?? 0} label="Pipelines" />
            <Metric value={pipelines.data?.nodes.length ?? 0} label="Workflows" />
            <Metric value={pipelines.data?.edges.length ?? 0} label="Links" />
          </div>
        );
      case "fleet":
        return (
          <div className="dash-metric-row">
            <Metric value={repos.data?.length ?? 0} label="Repositories watched" />
          </div>
        );
    }
  }

  const visibleOrder = layout.order.filter(isVisible);

  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Overview</h2>
          <div className="sub">Your fleet at a glance — click any card to dive in.</div>
        </div>
        <button
          className={customizing ? "btn sm primary" : "btn sm"}
          onClick={() => setCustomizing((c) => !c)}
        >
          {customizing ? "Done" : "Customize"}
        </button>
      </div>

      {customizing && (
        <div className="card customize-panel">
          <div className="customize-head">
            <span className="settings-label">Widgets</span>
            <button className="btn sm ghost" onClick={reset}>
              Reset to default
            </button>
          </div>
          <ul className="customize-list">
            {layout.order.map((id, i) => (
              <li key={id}>
                <label className="customize-toggle">
                  <input type="checkbox" checked={isVisible(id)} onChange={() => toggle(id)} />
                  {WIDGET_META[id].title}
                </label>
                <span className="customize-move">
                  <button
                    className="icon-btn"
                    onClick={() => move(id, -1)}
                    disabled={i === 0}
                    aria-label="Move up"
                    title="Move up"
                  >
                    <IconChevron className="rot-up" />
                  </button>
                  <button
                    className="icon-btn"
                    onClick={() => move(id, 1)}
                    disabled={i === layout.order.length - 1}
                    aria-label="Move down"
                    title="Move down"
                  >
                    <IconChevron className="rot-down" />
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {visibleOrder.length === 0 ? (
        <div className="empty" style={{ marginTop: 8 }}>
          <IconLayers size={30} className="empty-icon" />
          <h3>No widgets shown</h3>
          <p>Turn some on with Customize.</p>
        </div>
      ) : (
        <div className="dash-grid">
          {visibleOrder.map((id) => (
            <div className="card dash-card" key={id}>
              <button className="dash-card-head" onClick={() => onNavigate(WIDGET_META[id].tab)} title={`Open ${WIDGET_META[id].title}`}>
                <span>{WIDGET_META[id].title}</span>
                <IconArrowRight size={13} className="dash-card-go" />
              </button>
              <div className="dash-card-body">{renderWidget(id)}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
