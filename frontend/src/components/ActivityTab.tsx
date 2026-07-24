import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { relativeTime } from "../lib/format";
import { promptForToken, useHasOperateToken } from "../lib/auth";
import {
  EmptyState,
  ErrorBanner,
  ExternalLink,
  IconActivity,
  IconExternal,
  IconKey,
  TableSkeleton,
} from "./ui";
import type { AuditLogEntry } from "../types";

/** Group an action ("campaign.apply") into a category for the icon/colour + filter chips. */
function category(action: string): string {
  const head = action.split(".")[0];
  if (head === "sarif" || head === "audit") return "audit";
  if (head === "template" || head === "binding") return "drift";
  if (head === "governance") return "governance";
  if (head === "notification") return "notification";
  if (head === "repo" || head === "offline") return "fleet";
  if (head === "run") return "run";
  return head;
}

/** Plain-language verb for an action code. Falls back to the raw code prettified. */
const ACTION_LABEL: Record<string, string> = {
  "repo.add": "Added repository",
  "repo.remove": "Removed repository",
  "offline.sync": "Synced offline repos",
  "run.rerun": "Re-ran workflow run",
  "sarif.upload": "Uploaded SARIF to Code Scanning",
  "sarif.ingest": "Ingested external SARIF",
  "governance.require_check": "Required a status check",
  "template.create": "Created drift template",
  "binding.create": "Bound repo to template",
  "campaign.create": "Created campaign",
  "campaign.apply": "Applied campaign",
  "notification.create": "Added alert channel",
  "notification.update": "Updated alert channel",
  "notification.delete": "Removed alert channel",
};

function actionLabel(action: string): string {
  return ACTION_LABEL[action] ?? action.replace(/[._]/g, " ");
}

const CATEGORIES: { key: string; label: string }[] = [
  { key: "all", label: "All" },
  { key: "campaign", label: "Campaigns" },
  { key: "audit", label: "Audit" },
  { key: "drift", label: "Drift" },
  { key: "governance", label: "Governance" },
  { key: "fleet", label: "Fleet" },
  { key: "notification", label: "Alerts" },
  { key: "run", label: "Runs" },
];

/** Pull a PR URL out of the free-form detail blob, if one is there (campaign entries carry them). */
function prUrl(detail: Record<string, unknown> | null): string | null {
  if (!detail) return null;
  const u = detail.pr_url ?? detail.url;
  return typeof u === "string" && u.startsWith("http") ? u : null;
}

export function ActivityTab() {
  const hasToken = useHasOperateToken();
  const [cat, setCat] = useState("all");
  const [q, setQ] = useState("");
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["audit-log"],
    queryFn: () => api.auditLog({ limit: 250 }),
    enabled: hasToken,
  });

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (data ?? []).filter((e) => {
      if (cat !== "all" && category(e.action) !== cat) return false;
      if (!needle) return true;
      return (
        e.actor.toLowerCase().includes(needle) ||
        e.action.toLowerCase().includes(needle) ||
        (e.target ?? "").toLowerCase().includes(needle)
      );
    });
  }, [data, cat, q]);

  // The trail is operator-level (it names PR URLs + targets), so it needs the operate token.
  if (!hasToken) {
    return (
      <section>
        <div className="content-head">
          <div>
            <h2>Activity</h2>
            <div className="sub">Every write ActionsPlane made to your fleet — who, what, when.</div>
          </div>
        </div>
        <EmptyState icon={<IconActivity size={34} />} title="Set an operate token to view activity">
          The activity trail names targets and pull-request URLs, so it's gated like a write. Set your
          operate token to load it.
        </EmptyState>
        <div style={{ textAlign: "center", marginTop: -8 }}>
          <button className="btn sm primary" onClick={promptForToken}>
            <IconKey size={13} /> Set operate token
          </button>
        </div>
      </section>
    );
  }

  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Activity</h2>
          <div className="sub">Every write ActionsPlane made to your fleet — who, what, when.</div>
        </div>
      </div>

      <div className="activity-controls">
        <div className="seg activity-cats">
          {CATEGORIES.map((c) => (
            <button
              key={c.key}
              className={cat === c.key ? "seg-btn active" : "seg-btn"}
              onClick={() => setCat(c.key)}
            >
              {c.label}
            </button>
          ))}
        </div>
        <input
          className="settings-input activity-search"
          placeholder="Filter by actor, action, or target…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      {isError ? (
        <ErrorBanner error={error} />
      ) : isLoading ? (
        <TableSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState icon={<IconActivity size={34} />} title="Nothing here yet">
          {data && data.length > 0
            ? "No activity matches this filter."
            : "Write actions — campaigns, fix PRs, required checks, repo changes — show up here as they happen."}
        </EmptyState>
      ) : (
        <ol className="activity-feed">
          {rows.map((e: AuditLogEntry) => {
            const pr = prUrl(e.detail);
            return (
              <li key={e.id} className={`activity-item cat-${category(e.action)}`}>
                <span className="activity-dot" />
                <div className="activity-main">
                  <div className="activity-line">
                    <span className="activity-action">{actionLabel(e.action)}</span>
                    {e.target && <span className="activity-target mono">{e.target}</span>}
                  </div>
                  <div className="activity-meta">
                    <span className="activity-actor">{e.actor}</span>
                    <span className="activity-sep">·</span>
                    <time title={e.occurred_at}>{relativeTime(e.occurred_at)}</time>
                    {pr && (
                      <>
                        <span className="activity-sep">·</span>
                        <ExternalLink href={pr}>
                          view PR <IconExternal size={11} />
                        </ExternalLink>
                      </>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
