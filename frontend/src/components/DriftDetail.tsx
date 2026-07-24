import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { DriftPill, ErrorBanner, IconArrowRight, TableSkeleton } from "./ui";

type DiffRow = { t: "ctx" | "add" | "del"; text: string };

/** Minimal LCS line diff — canonical (a) vs current (b). Small workflow files, so O(n·m) is fine. */
function lineDiff(aStr: string, bStr: string): DiffRow[] {
  const a = aStr.replace(/\n$/, "").split("\n");
  const b = bStr.replace(/\n$/, "").split("\n");
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: DiffRow[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) out.push({ t: "ctx", text: a[i++] }), j++;
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ t: "del", text: a[i++] });
    else out.push({ t: "add", text: b[j++] });
  }
  while (i < n) out.push({ t: "del", text: a[i++] });
  while (j < m) out.push({ t: "add", text: b[j++] });
  return out;
}

/** Full-page drift detail (rendered inside the content area, replacing the binding list). */
export function DriftDetail({ bindingId, onClose }: { bindingId: number; onClose: () => void }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["drift-detail", bindingId],
    queryFn: () => api.driftDetail(bindingId),
  });

  const diff = data ? lineDiff(data.canonical_yaml, data.candidate_yaml) : [];
  const added = diff.filter((r) => r.t === "add").length;
  const removed = diff.filter((r) => r.t === "del").length;

  return (
    <section className="drift-page">
      <div className="content-head">
        <div style={{ display: "flex", alignItems: "center", gap: 14, minWidth: 0 }}>
          <button className="btn sm" onClick={onClose} title="Back to drift list">
            <IconArrowRight className="flip" /> Back
          </button>
          <div style={{ minWidth: 0 }}>
            <h2>Workflow drift</h2>
            <div className="sub mono">
              {data ? `${data.repo} · ${data.path}` : "loading…"}
            </div>
          </div>
        </div>
        {data && (
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span className="subtle mono" style={{ fontSize: 11 }}>
              +{added} −{removed}
            </span>
            <DriftPill severity={data.severity} />
          </div>
        )}
      </div>

      {isError ? (
        <ErrorBanner error={error} />
      ) : isLoading || !data ? (
        <TableSkeleton rows={8} />
      ) : (
        <div className="drift-grid">
          <div className="card drift-changes-card">
            <div className="drift-card-title">
              What changed{data.changes.length > 0 ? ` · ${data.changes.length}` : ""}
            </div>
            {data.changes.length === 0 ? (
              <p className="muted" style={{ padding: "0 4px" }}>
                No structural changes — this workflow matches the canonical template{" "}
                <span className="mono">{data.template}</span>.
              </p>
            ) : (
              <ul className="drift-changes">
                {data.changes.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            )}
            <div className="drift-legend">
              <span className="mono">
                vs template <strong>{data.template}</strong>
              </span>
            </div>
          </div>

          <div className="card diff-card">
            <div className="drift-card-title">
              Diff <span className="subtle" style={{ textTransform: "none", letterSpacing: 0 }}>· canonical → current</span>
            </div>
            <div className="diff diff-page">
              {diff.map((row, i) => (
                <div key={i} className={`diff-line ${row.t}`}>
                  <span className="diff-gutter">
                    {row.t === "add" ? "+" : row.t === "del" ? "−" : " "}
                  </span>
                  <span className="diff-text">{row.text || " "}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
