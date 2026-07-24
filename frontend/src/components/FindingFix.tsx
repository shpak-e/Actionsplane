import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { promptForToken, useHasOperateToken } from "../lib/auth";
import { operationLabel } from "../lib/labels";
import { ExternalLink, IconArrowRight, IconCheck, IconExternal, IconKey, IconX } from "./ui";
import type { Finding } from "../types";

/** Per-finding remediation. `op` names an implemented bulk-edit operation when one exists — those
 * findings get a one-click "Open fix PR"; the rest show manual guidance (no automated fixer yet). */
const REMEDIATION: Record<string, { title: string; steps: string; op?: string }> = {
  unpinned_action: {
    title: "Pin to a commit SHA",
    steps:
      "Replace the tag/branch ref with the action's full 40-character commit SHA, so a moved tag can't change what runs.",
    op: "pin-shas",
  },
  unverified_publisher: {
    title: "Review the publisher",
    steps: "Prefer actions from GitHub-verified publishers, or pin to a reviewed commit SHA.",
  },
  missing_permissions: {
    title: "Set least-privilege permissions",
    steps:
      "Add a top-level permissions: block (e.g. contents: read) so GITHUB_TOKEN doesn't fall back to the repo default (often write-all).",
    op: "set-permissions",
  },
  broad_permissions: {
    title: "Narrow the permissions",
    steps: "Replace write-all / broad scopes with only the permissions the workflow actually uses.",
    op: "set-permissions",
  },
  deprecated_action: {
    title: "Upgrade the action",
    steps: "Bump the action to its current major version — the pinned one is deprecated.",
  },
  dangerous_secret_flow: {
    title: "Contain the secret",
    steps:
      "Don't expose secrets to pull_request-triggered steps or third-party actions that can exfiltrate them.",
  },
  missing_concurrency: {
    title: "Add a concurrency group",
    steps:
      "Add a concurrency: block with cancel-in-progress so overlapping runs on the same ref can't race.",
    op: "add-concurrency",
  },
  dangling_workflow_run: {
    title: "Fix the workflow_run reference",
    steps:
      "This workflow_run trigger names an upstream workflow that doesn't exist in the repo, so it never fires. Match the name: field of the intended upstream workflow exactly, or remove the trigger.",
  },
  missing_reusable_workflow: {
    title: "Restore the reusable workflow",
    steps:
      "This job calls a local reusable workflow file that isn't in the repo, so the job can't start. Add the missing .github/workflows file, or correct the uses: path.",
  },
};

export function FindingFix({ finding }: { finding: Finding }) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const hasToken = useHasOperateToken();
  const qc = useQueryClient();

  const info = REMEDIATION[finding.finding_type] ?? {
    title: "Manual remediation",
    steps: "Review this finding and apply the appropriate fix in the workflow file.",
  };

  const fix = useMutation({
    mutationFn: async () => {
      const campaign = await api.createCampaign({
        name: `fix ${finding.finding_type} · repo ${finding.repo_id}`,
        operation: info.op as string,
        repo_ids: [finding.repo_id],
      });
      const applied = await api.applyCampaign(campaign.id);
      return applied.targets.find((t) => t.repo_id === finding.repo_id) ?? applied.targets[0] ?? null;
    },
    onSuccess: () => {
      for (const k of ["findings", "scorecard"]) qc.invalidateQueries({ queryKey: [k] });
    },
  });
  const target = fix.data;

  // Anchor a FIXED-position popover to the button so the table's overflow:hidden can't clip it.
  function toggle() {
    if (pos) {
      setPos(null);
      return;
    }
    const r = btnRef.current?.getBoundingClientRect();
    if (!r) return;
    const width = 300;
    const left = Math.max(8, Math.min(r.right - width, window.innerWidth - width - 8));
    const opensUp = r.bottom + 260 > window.innerHeight;
    const top = opensUp ? Math.max(8, r.top - 8) : r.bottom + 6;
    setPos({ top, left });
  }

  return (
    <>
      <button
        ref={btnRef}
        className={info.op ? "btn sm fix-cta" : "btn sm"}
        onClick={toggle}
        aria-expanded={pos != null}
        title={info.op ? "One-click fix — opens a pull request" : "How to fix this finding"}
      >
        Fix{info.op ? " ▸" : ""}
      </button>

      {pos != null &&
        createPortal(
          <>
            <div className="fix-scrim" onClick={() => setPos(null)} />
            <div
              className="fix-pop"
              role="dialog"
              aria-label="How to fix"
              style={{ top: pos.top, left: pos.left }}
            >
              <div className="settings-label">{info.title}</div>
              <p className="settings-hint">{info.steps}</p>

              {info.op ? (
                <>
                  <p className="settings-hint subtle" style={{ marginTop: 2 }}>
                    Fix operation: <span className="mono">{operationLabel(info.op)}</span>
                  </p>
                  {hasToken ? (
                    <button
                      className="btn sm primary"
                      disabled={fix.isPending || fix.isSuccess}
                      onClick={() => fix.mutate()}
                      title="Open a pull request that applies the fix"
                    >
                      <IconArrowRight /> {fix.isPending ? "Opening PR…" : "Open fix PR"}
                    </button>
                  ) : (
                    <button className="btn sm primary" onClick={promptForToken}>
                      <IconKey /> Set operate token to fix
                    </button>
                  )}
                  {fix.isError && (
                    <div className="toast err">
                      <IconX /> {(fix.error as Error).message}
                    </div>
                  )}
                  {target?.pr_url && (
                    <div className="toast ok">
                      <IconCheck />{" "}
                      <ExternalLink href={target.pr_url}>
                        PR #{target.pr_number} opened <IconExternal />
                      </ExternalLink>
                    </div>
                  )}
                  {fix.isSuccess && !target?.pr_url && (
                    <div className="toast err">
                      <IconX /> {target?.error ?? "No PR was opened (nothing to change, or a conflict)."}
                    </div>
                  )}
                </>
              ) : (
                <p className="settings-hint subtle">
                  No automated fixer for this type yet — apply the change manually in the workflow file.
                </p>
              )}
            </div>
          </>,
          document.body,
        )}
    </>
  );
}
