import { useQuery } from "@tanstack/react-query";
import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { api } from "../api";
import { EmptyState, ErrorBanner, IconGraph, TableSkeleton } from "./ui";
import type { PipelineEdge, PipelineGraph, PipelineNode } from "../types";

// Edge type → human label + the semantic colour class used for the connector + chip.
const EDGE_META: Record<string, { label: string; cls: string }> = {
  triggers: { label: "triggers", cls: "triggers" },
  calls: { label: "calls reusable", cls: "calls" },
  "opens-pr": { label: "opens PR", cls: "opens-pr" },
  dispatch: { label: "dispatches", cls: "dispatch" },
};

// A small, theme-aligned palette. Each distinct repo gets a stable colour so a cross-repo hop
// (the thing that was hard to see before) reads at a glance: the card colour changes across the arrow.
const REPO_PALETTE = ["#5b9dff", "#3fb950", "#d29922", "#bc8cff", "#ff7b72", "#56d4dd", "#e3b341"];

function buildRepoColors(nodes: PipelineNode[]): Map<string, string> {
  const repos = Array.from(new Set(nodes.map((n) => n.repo))).sort();
  return new Map(repos.map((r, i) => [r, REPO_PALETTE[i % REPO_PALETTE.length]]));
}

/** Longest-path layering: layer(n) = max(layer(pred)) + 1, so flow reads left→right. */
function computeLayers(ids: string[], edges: PipelineEdge[]): Map<string, number> {
  const inComp = new Set(ids);
  const succ = new Map<string, string[]>(ids.map((id) => [id, []]));
  const indeg = new Map<string, number>(ids.map((id) => [id, 0]));
  for (const e of edges) {
    if (!inComp.has(e.source) || !inComp.has(e.target) || e.source === e.target) continue;
    succ.get(e.source)!.push(e.target);
    indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
  }
  const layer = new Map<string, number>(ids.map((id) => [id, 0]));
  const work = new Map(indeg);
  const queue = ids.filter((id) => (indeg.get(id) ?? 0) === 0);
  const seen = new Set<string>();
  while (queue.length) {
    const n = queue.shift()!;
    if (seen.has(n)) continue;
    seen.add(n);
    for (const m of succ.get(n)!) {
      layer.set(m, Math.max(layer.get(m)!, layer.get(n)! + 1));
      work.set(m, work.get(m)! - 1);
      if (work.get(m)! === 0) queue.push(m);
    }
  }
  return layer; // nodes inside a cycle keep layer 0 (rare; degrades to a single column)
}

// Map a node's latest-run outcome to a status pill {label, css class}. Null for external/unobserved.
function nodeStatus(node: PipelineNode): { label: string; cls: string } | null {
  if (node.conclusion) {
    const c = node.conclusion;
    if (c === "success") return { label: "passed", cls: "ok" };
    if (c === "failure" || c === "timed_out") return { label: "failed", cls: "fail" };
    if (c === "cancelled" || c === "skipped") return { label: c, cls: "neutral" };
    return { label: c, cls: "neutral" };
  }
  if (node.status === "in_progress") return { label: "running", cls: "running" };
  if (node.status === "queued") return { label: "queued", cls: "neutral" };
  return null;
}

function NodeCard({
  node,
  color,
  nodeRef,
}: {
  node: PipelineNode;
  color: string;
  nodeRef: (el: HTMLDivElement | null) => void;
}) {
  const status = nodeStatus(node);
  const failed = node.conclusion === "failure";
  return (
    <div
      ref={nodeRef}
      className={`pflow-node${node.external ? " external" : ""}${failed ? " failed" : ""}`}
      style={{ ["--repo" as string]: color }}
    >
      <div className="pflow-node-top">
        <span className="pflow-node-name">{node.name}</span>
        {status && <span className={`badge ${status.cls}`}>{status.label}</span>}
      </div>
      <div className="pflow-node-repo">
        <span className="pflow-dot" style={{ background: color }} />
        {node.repo}
        {node.external && <span className="pnode-ext">external</span>}
      </div>
      {failed && node.failed_step && (
        <div className="pflow-node-fail" title="Failing step in the latest run">
          ✕ {node.failed_job ? `${node.failed_job} › ` : ""}
          <strong>{node.failed_step}</strong>
        </div>
      )}
      {node.badges.length > 0 && (
        <div className="pflow-node-badges">
          {node.badges.map((b) => (
            <span key={b} className="chip">
              {b}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

type EdgeGeom = { d: string; midX: number; midY: number; cls: string; label: string; heuristic: boolean };
type Geom = { edges: EdgeGeom[]; w: number; h: number };

// Geometry is recomputed on every layout pass; only commit it to state when it actually changed,
// otherwise setGeom on a fresh object each render creates an infinite render loop (blank tab).
function sameGeom(a: Geom, b: Geom): boolean {
  if (a.w !== b.w || a.h !== b.h || a.edges.length !== b.edges.length) return false;
  return a.edges.every((e, i) => {
    const o = b.edges[i];
    return e.d === o.d && e.cls === o.cls && e.label === o.label && e.heuristic === o.heuristic;
  });
}

function PipelineFlow({
  ids,
  edges,
  nodesById,
  repoColors,
}: {
  ids: string[];
  edges: PipelineEdge[];
  nodesById: Map<string, PipelineNode>;
  repoColors: Map<string, string>;
}) {
  const compEdges = edges.filter((e) => ids.includes(e.source) && ids.includes(e.target));
  const layers = computeLayers(ids, compEdges);
  const maxLayer = Math.max(0, ...ids.map((id) => layers.get(id) ?? 0));
  // group node ids into columns by layer, stable-sorted within a column
  const columns: string[][] = Array.from({ length: maxLayer + 1 }, () => []);
  for (const id of ids) columns[layers.get(id) ?? 0].push(id);
  columns.forEach((col) =>
    col.sort((a, b) => {
      const na = nodesById.get(a)!,
        nb = nodesById.get(b)!;
      return na.repo.localeCompare(nb.repo) || na.name.localeCompare(nb.name);
    }),
  );

  const containerRef = useRef<HTMLDivElement | null>(null);
  const nodeEls = useRef<Map<string, HTMLDivElement>>(new Map());
  const [geom, setGeom] = useState<Geom>({ edges: [], w: 0, h: 0 });

  // offset of an element relative to the flow container (sum offsets up the offsetParent chain)
  const offsetWithin = (el: HTMLElement, container: HTMLElement) => {
    let x = 0,
      y = 0;
    let cur: HTMLElement | null = el;
    while (cur && cur !== container) {
      x += cur.offsetLeft;
      y += cur.offsetTop;
      cur = cur.offsetParent as HTMLElement | null;
    }
    return { x, y, w: el.offsetWidth, h: el.offsetHeight };
  };

  const recompute = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const out: EdgeGeom[] = [];
    for (const e of compEdges) {
      const s = nodeEls.current.get(e.source);
      const t = nodeEls.current.get(e.target);
      if (!s || !t) continue;
      const a = offsetWithin(s, container);
      const b = offsetWithin(t, container);
      const x1 = a.x + a.w,
        y1 = a.y + a.h / 2;
      const x2 = b.x,
        y2 = b.y + b.h / 2;
      const dx = Math.max(28, (x2 - x1) / 2);
      const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
      const meta = EDGE_META[e.type] ?? { label: e.type, cls: "neutral" };
      out.push({
        d,
        midX: (x1 + x2) / 2,
        midY: (y1 + y2) / 2,
        cls: meta.cls,
        label: meta.label,
        heuristic: e.heuristic,
      });
    }
    const next: Geom = { edges: out, w: container.scrollWidth, h: container.scrollHeight };
    setGeom((prev) => (sameGeom(prev, next) ? prev : next)); // bail when unchanged → no re-render loop
  }, [compEdges]);

  useLayoutEffect(() => {
    recompute();
    const ro = new ResizeObserver(recompute);
    if (containerRef.current) ro.observe(containerRef.current);
    window.addEventListener("resize", recompute);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", recompute);
    };
  }, [recompute]);

  return (
    <div className="pflow" ref={containerRef}>
      <svg className="pflow-svg" width={geom.w} height={geom.h}>
        <defs>
          {["triggers", "calls", "opens-pr", "dispatch", "neutral"].map((c) => (
            <marker
              key={c}
              id={`arrow-${c}`}
              markerWidth="8"
              markerHeight="8"
              refX="7"
              refY="4"
              orient="auto"
            >
              <path d="M0,0 L8,4 L0,8 Z" className={`pflow-arrow ${c}`} />
            </marker>
          ))}
        </defs>
        {geom.edges.map((e, i) => (
          <path
            key={i}
            d={e.d}
            className={`pflow-path ${e.cls}${e.heuristic ? " heuristic" : ""}`}
            markerEnd={`url(#arrow-${e.cls})`}
            fill="none"
          />
        ))}
      </svg>
      {geom.edges.map((e, i) => (
        <span
          key={i}
          className={`pflow-edge-label ${e.cls}`}
          style={{ left: e.midX, top: e.midY }}
        >
          {e.label}
          {e.heuristic && <span className="pflow-heur" title="Heuristic (pattern-matched)">~</span>}
        </span>
      ))}
      {columns.map((col, ci) => (
        <div className="pflow-col" key={ci}>
          {col.map((id) => {
            const node = nodesById.get(id)!;
            return (
              <NodeCard
                key={id}
                node={node}
                color={repoColors.get(node.repo) ?? "var(--accent)"}
                nodeRef={(el) => {
                  if (el) nodeEls.current.set(id, el);
                  else nodeEls.current.delete(id);
                }}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}

export function PipelinesTab() {
  const { data, isLoading, isError, error } = useQuery<PipelineGraph>({
    queryKey: ["pipelines"],
    queryFn: api.pipelines,
  });

  const nodes = data?.nodes ?? [];
  const nodesById = new Map(nodes.map((n) => [n.id, n]));
  const pipelines = data?.pipelines ?? [];
  const edges = data?.edges ?? [];
  const repoColors = buildRepoColors(nodes);
  const repos = Array.from(repoColors.entries());

  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Pipelines</h2>
          <div className="sub">Cross-workflow trigger &amp; dependency chains across the fleet</div>
        </div>
      </div>

      {isError ? (
        <ErrorBanner error={error} />
      ) : isLoading ? (
        <TableSkeleton />
      ) : pipelines.length === 0 ? (
        <EmptyState icon={<IconGraph size={34} />} title="No pipelines detected">
          ActionsPlane links workflows by <span className="mono">workflow_run</span> triggers,
          reusable-workflow <span className="mono">uses:</span> calls, and cross-repo PR/dispatch
          steps. Sync some repos (or seed demo data) to populate this graph.
        </EmptyState>
      ) : (
        <>
          <div className="pflow-legend card">
            <div className="pflow-legend-group">
              <span className="pflow-legend-title">Repos</span>
              {repos.map(([repo, color]) => (
                <span key={repo} className="pflow-legend-item">
                  <span className="pflow-dot" style={{ background: color }} />
                  <span className="mono">{repo}</span>
                </span>
              ))}
            </div>
            <div className="pflow-legend-group">
              <span className="pflow-legend-title">Links</span>
              {Object.entries(EDGE_META).map(([type, meta]) => (
                <span key={type} className="pflow-legend-item">
                  <span className={`pflow-legend-line ${meta.cls}`} />
                  {meta.label}
                </span>
              ))}
              <span className="pflow-legend-item subtle">
                <span className="pflow-heur">~</span> heuristic
              </span>
            </div>
          </div>

          <div className="pipelines">
            {pipelines.map((ids, i) => {
              const idSet = new Set(ids);
              const linkCount = edges.filter((e) => idSet.has(e.source) && idSet.has(e.target)).length;
              return (
                <div className="card pipeline" key={i}>
                  <div className="pipeline-head">
                    Pipeline {i + 1}
                    <span className="subtle">
                      {" "}
                      · {ids.length} workflows · {linkCount} link{linkCount === 1 ? "" : "s"}
                    </span>
                  </div>
                  <PipelineFlow
                    ids={ids}
                    edges={edges}
                    nodesById={nodesById}
                    repoColors={repoColors}
                  />
                </div>
              );
            })}
            <p className="subtle" style={{ fontSize: 12, marginTop: 4 }}>
              Flow reads left→right (upstream → downstream). Card colour = repo, so a colour change
              across an arrow is a cross-repo hop. <strong>~</strong> marks heuristic edges
              (PR/dispatch senders detected from step patterns); trigger and reusable-call edges are
              read precisely from <span className="mono">on:</span> / <span className="mono">uses:</span>.
            </p>
          </div>
        </>
      )}
    </section>
  );
}
