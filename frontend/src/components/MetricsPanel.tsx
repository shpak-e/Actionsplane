import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { fmtPct, fmtSecs } from "../lib/format";

export function MetricsPanel({ workflowId }: { workflowId: number }) {
  const { data: m } = useQuery({
    queryKey: ["metrics", workflowId],
    queryFn: () => api.metrics(workflowId),
  });
  if (!m) return null;

  const cards: [string, string][] = [
    ["Success rate", fmtPct(m.success_rate)],
    ["Runs", String(m.runs)],
    ["p50 duration", fmtSecs(m.p50_duration_s)],
    ["p95 duration", fmtSecs(m.p95_duration_s)],
    ["p95 queue", fmtSecs(m.p95_queue_s)],
    ["Flake rate", fmtPct(m.flake_rate)],
  ];

  return (
    <div className="metrics-grid">
      {cards.map(([label, value]) => (
        <div className="stat" key={label}>
          <span className="stat-label">{label}</span>
          <div className="stat-value">{value}</div>
        </div>
      ))}
    </div>
  );
}
