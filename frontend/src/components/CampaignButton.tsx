import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useHasOperateToken } from "../lib/auth";
import { useRepos } from "../hooks/useRepos";
import { ExternalLink, IconArrowRight, IconCheck, IconExternal, IconX } from "./ui";

/**
 * One-click fleet remediation: create a campaign (computes the dry-run diff, no writes), show the
 * per-repo preview, then open PRs on explicit confirm. Shared by the Policy simulator and the
 * Deprecation Radar so a fixable rule/entry becomes a reviewed PR wave in two clicks.
 */
export function CampaignButton({
  name,
  operation,
  repoIds,
  params,
  label = "Create fix campaign",
}: {
  name: string;
  operation: string;
  repoIds: number[];
  params?: Record<string, unknown> | null;
  label?: string;
}) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const hasToken = useHasOperateToken();
  const { byId } = useRepos();
  const qc = useQueryClient();

  const create = useMutation({
    mutationFn: () => api.createCampaign({ name, operation, repo_ids: repoIds, params }),
  });
  const apply = useMutation({
    mutationFn: (id: number) => api.applyCampaign(id),
    onSuccess: () => {
      for (const k of ["findings", "scorecard", "drift"]) qc.invalidateQueries({ queryKey: [k] });
    },
  });

  const campaign = apply.data ?? create.data;
  const ready = (create.data?.targets ?? []).filter(
    (t) => t.status === "dry-run-ok" && t.diff_preview,
  );

  function open() {
    const r = btnRef.current?.getBoundingClientRect();
    if (!r) return;
    const width = 340;
    const left = Math.max(8, Math.min(r.right - width, window.innerWidth - width - 8));
    const top = r.bottom + 260 > window.innerHeight ? Math.max(8, r.top - 8) : r.bottom + 6;
    setPos({ top, left });
    if (!create.data && !create.isPending) create.mutate();
  }

  return (
    <>
      <button
        ref={btnRef}
        className="btn sm primary"
        onClick={() => (pos ? setPos(null) : open())}
        disabled={!hasToken || repoIds.length === 0}
        title={
          !hasToken
            ? "Set an operate token first"
            : repoIds.length === 0
              ? "No fixable repos"
              : `${label} across ${repoIds.length} repo(s)`
        }
      >
        {label}
      </button>

      {pos != null &&
        createPortal(
          <>
            <div className="fix-scrim" onClick={() => setPos(null)} />
            <div className="fix-pop campaign-pop" role="dialog" style={{ top: pos.top, left: pos.left }}>
              <div className="settings-label">
                {operation} · {repoIds.length} repo{repoIds.length === 1 ? "" : "s"}
              </div>

              {create.isPending && <p className="settings-hint">Computing dry-run diffs…</p>}
              {create.isError && (
                <div className="toast err">
                  <IconX /> {(create.error as Error).message}
                </div>
              )}

              {create.data && (
                <>
                  <p className="settings-hint">
                    {ready.length} of {create.data.targets.length} repo
                    {create.data.targets.length === 1 ? "" : "s"} have changes to apply.
                  </p>
                  <ul className="campaign-targets">
                    {campaign!.targets.map((t) => {
                      const repo = byId.get(t.repo_id);
                      const nm = repo ? `${repo.owner}/${repo.name}` : `#${t.repo_id}`;
                      return (
                        <li key={t.id}>
                          <span className="mono ct-name">{nm}</span>
                          {t.pr_url ? (
                            <ExternalLink href={t.pr_url}>
                              PR #{t.pr_number} <IconExternal />
                            </ExternalLink>
                          ) : t.error ? (
                            <span className="ct-err mono">{t.error.slice(0, 40)}</span>
                          ) : t.diff_preview ? (
                            <span className="ct-ok mono">ready</span>
                          ) : (
                            <span className="subtle mono">no change</span>
                          )}
                        </li>
                      );
                    })}
                  </ul>

                  {!apply.data && ready.length > 0 && (
                    <button
                      className="btn sm primary"
                      disabled={apply.isPending}
                      onClick={() => apply.mutate(create.data!.id)}
                    >
                      <IconArrowRight /> {apply.isPending ? "Opening PRs…" : `Open ${ready.length} PR${ready.length === 1 ? "" : "s"}`}
                    </button>
                  )}
                  {apply.isError && (
                    <div className="toast err">
                      <IconX /> {(apply.error as Error).message}
                    </div>
                  )}
                  {apply.data && (
                    <div className="toast ok">
                      <IconCheck /> Opened {apply.data.targets.filter((t) => t.pr_url).length} PR(s).
                    </div>
                  )}
                </>
              )}
            </div>
          </>,
          document.body,
        )}
    </>
  );
}
