import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { ErrorBanner, TableSkeleton } from "./ui";
import type { OpenApiOperation } from "../types";

type View = "guide" | "api";

const METHOD_ORDER = ["get", "post", "patch", "put", "delete"];

/** Curated product usage guide (kept in the app so it ships with the deploy). */
function Guide() {
  return (
    <div className="doc-prose">
      <h3>What ActionsPlane is</h3>
      <p>
        ActionsPlane is a control plane for your fleet of GitHub Actions workflows. It{" "}
        <strong>observes</strong> every run, <strong>audits</strong> workflows for supply-chain and
        hygiene problems, tracks <strong>drift</strong> from your canonical templates and upcoming{" "}
        <strong>deprecations</strong>, and lets you <strong>fix</strong> issues across many repos at
        once — always through reviewable pull requests.
      </p>

      <h3>Getting started</h3>
      <ol>
        <li>
          <strong>Add repositories.</strong> Go to <em>Settings → Repositories</em> and add the
          repos you want to watch (or install the GitHub App to onboard them automatically).
        </li>
        <li>
          <strong>Set an operate token.</strong> Click the key icon (top-right) and paste your{" "}
          <code>ACTIONSPLANE_API_TOKEN</code>. It’s stored only in your browser and enables the
          write controls (opening fix PRs, running campaigns, managing settings).
        </li>
        <li>
          <strong>Route alerts.</strong> In <em>Settings → Notifications</em>, connect Slack, a
          webhook, or email and choose which events should notify you.
        </li>
      </ol>

      <h3>The core loop: observe → audit → fix</h3>
      <p>
        The whole product is one loop. An <em>audit lens</em> (Security, Drift, Radar, or Policy)
        surfaces a problem across the fleet; a one-click <strong>Fix</strong> turns it into a{" "}
        <strong>campaign</strong> — a dry-run diff per repo that you review, then apply as a wave of
        pull requests. Nothing is changed without a PR.
      </p>

      <h3>The tabs</h3>
      <dl className="doc-defs">
        <dt>Home</dt>
        <dd>Your customizable overview — widgets for posture, drift, deadlines, runs and more. Click <em>Customize</em> to show/hide and reorder cards.</dd>
        <dt>Runs / Pipelines</dt>
        <dd><strong>Observe.</strong> Live run history (filter by repo, workflow, status) and a cross-repo graph of how workflows trigger and call each other.</dd>
        <dt>Security</dt>
        <dd><strong>Audit.</strong> Supply-chain &amp; hygiene findings with a posture score. Each fixable finding has a <strong>Fix ▸</strong> button that opens a PR (e.g. pin an action to a SHA, add a least-privilege permissions block).</dd>
        <dt>Drift</dt>
        <dd><strong>Audit.</strong> How far each workflow has diverged from its canonical template. Click a row for a side-by-side diff.</dd>
        <dt>Radar</dt>
        <dd><strong>Audit.</strong> Dated GitHub deprecations (runner images, action majors) joined against your fleet, sorted by deadline, each with a one-click migration.</dd>
        <dt>Policy</dt>
        <dd><strong>Audit.</strong> Simulate a ruleset (require SHA-pinning, permissions blocks, disallow risky triggers) against the fleet <em>before</em> you enforce it — then fix what breaks.</dd>
        <dt>Campaigns</dt>
        <dd><strong>Act.</strong> Every fix wave lands here: per-repo dry-run diffs, then reviewed PRs, with live status.</dd>
        <dt>Settings</dt>
        <dd>Notifications, watched repositories, and access control.</dd>
      </dl>

      <h3>Fixing across the fleet</h3>
      <p>
        Fix buttons in Security, Radar and Policy all create <strong>campaigns</strong>. A campaign
        first computes a dry-run diff per repo (no writes); you review it, then <em>Apply</em> opens
        one pull request per repo reusing the exact reviewed changes. Bulk edits require{" "}
        <code>ACTIONSPLANE_BULK_EDITS_ENABLED=true</code> on the server plus an operate token.
      </p>

      <h3>Notifications</h3>
      <p>
        Connect a channel in <em>Settings → Notifications</em> and subscribe it to events: a new
        critical/high finding, a campaign PR opened or failed, a deprecation deadline going urgent,
        or a workflow run failing. Alerts fire <strong>once per event</strong> (de-duplicated), and
        delivery never blocks an audit or a campaign. Use <strong>Send test</strong> to verify a
        channel end-to-end. Email channels require SMTP configured on the server; Slack and webhook
        channels need only the destination URL.
      </p>

      <h3>Access</h3>
      <p>
        Today, writes are gated by a single <strong>operate token</strong>. If a read token is
        configured too, the dashboard is view-only without the operate token. Single sign-on (OIDC)
        and per-user roles are on the roadmap under <em>Settings → Access</em>.
      </p>
    </div>
  );
}

function methodClass(m: string): string {
  return (
    { get: "ok", post: "running", patch: "neutral", put: "neutral", delete: "fail" }[m] ?? "neutral"
  );
}

function OperationRow({ method, path, op }: { method: string; path: string; op: OpenApiOperation }) {
  const [open, setOpen] = useState(false);
  const params = op.parameters ?? [];
  const hasDetail = params.length > 0 || !!op.requestBody || !!op.description;
  return (
    <div className="api-op">
      <button className="api-op-head" onClick={() => hasDetail && setOpen((o) => !o)}>
        <span className={`badge ${methodClass(method)} api-method`}>{method.toUpperCase()}</span>
        <span className="mono api-path">{path}</span>
        <span className="api-summary subtle">{op.summary}</span>
      </button>
      {open && hasDetail && (
        <div className="api-op-body">
          {op.description && <p className="muted">{op.description}</p>}
          {params.length > 0 && (
            <>
              <div className="settings-label">Parameters</div>
              <ul className="api-params">
                {params.map((p) => (
                  <li key={`${p.in}:${p.name}`}>
                    <span className="mono">{p.name}</span>
                    <span className="subtle">
                      {" "}
                      · {p.in}
                      {p.schema?.type ? ` · ${p.schema.type}` : ""}
                      {p.required ? " · required" : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {op.requestBody && <p className="subtle mono">accepts a JSON request body</p>}
        </div>
      )}
    </div>
  );
}

function ApiReference() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["openapi"],
    queryFn: api.openapi,
    staleTime: 5 * 60_000,
  });

  const groups = useMemo(() => {
    if (!data?.paths) return [];
    const byTag = new Map<string, { method: string; path: string; op: OpenApiOperation }[]>();
    for (const [path, methods] of Object.entries(data.paths)) {
      // group by the first meaningful path segment after the /api/v1 prefix
      const seg = path.replace(/^\/api\/v1\//, "").split("/")[0] || "root";
      for (const [method, op] of Object.entries(methods)) {
        if (!METHOD_ORDER.includes(method)) continue;
        if (!byTag.has(seg)) byTag.set(seg, []);
        byTag.get(seg)!.push({ method, path, op });
      }
    }
    return [...byTag.entries()]
      .map(([tag, ops]) => ({
        tag,
        ops: ops.sort(
          (a, b) =>
            a.path.localeCompare(b.path) ||
            METHOD_ORDER.indexOf(a.method) - METHOD_ORDER.indexOf(b.method),
        ),
      }))
      .sort((a, b) => a.tag.localeCompare(b.tag));
  }, [data]);

  if (isError) return <ErrorBanner error={error} />;
  if (isLoading || !data) return <TableSkeleton rows={8} />;

  return (
    <div>
      <p className="settings-hint" style={{ marginBottom: 16 }}>
        The REST API (<span className="mono">{data.info?.title ?? "ActionsPlane API"}</span>, v
        {data.info?.version ?? "?"}) — every endpoint is under <span className="mono">/api/v1</span>{" "}
        and takes an <code>Authorization: Bearer &lt;token&gt;</code> header when a token is
        configured. Rendered live from the running server, so it always matches this deploy.
      </p>
      {groups.map((g) => (
        <div className="api-group" key={g.tag}>
          <div className="api-group-title mono">{g.tag}</div>
          <div className="api-op-list">
            {g.ops.map((o) => (
              <OperationRow key={`${o.method}:${o.path}`} method={o.method} path={o.path} op={o.op} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function DocsTab() {
  const [view, setView] = useState<View>("guide");
  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Documentation</h2>
          <div className="sub">How to use ActionsPlane, and the REST API reference.</div>
        </div>
      </div>

      <div className="settings-layout">
        <nav className="settings-nav">
          <button
            className={view === "guide" ? "settings-nav-item active" : "settings-nav-item"}
            onClick={() => setView("guide")}
          >
            <span className="settings-nav-label">Usage guide</span>
            <span className="settings-nav-hint">Product walkthrough</span>
          </button>
          <button
            className={view === "api" ? "settings-nav-item active" : "settings-nav-item"}
            onClick={() => setView("api")}
          >
            <span className="settings-nav-label">API reference</span>
            <span className="settings-nav-hint">Live REST endpoints</span>
          </button>
        </nav>
        <div className="settings-body">{view === "guide" ? <Guide /> : <ApiReference />}</div>
      </div>
    </section>
  );
}
