import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { CampaignButton } from "./CampaignButton";
import { RequireCheckPanel } from "./RequireCheckPanel";
import { EmptyState, ErrorBanner, IconCheck, IconShield } from "./ui";
import type { RuleImpact } from "../types";

const RULE_LABEL: Record<string, string> = {
  require_sha_pinned: "Require SHA-pinned actions",
  require_permissions: "Require a permissions block",
  disallowed_trigger: "Disallow risky triggers",
};

/** SHA-pin readiness — auto-runs on mount. GitHub's org "require SHA-pinned actions" policy has been
 * GA since Aug 2025, and Dependabot/Renovate bump tags but can't pin to a SHA — so this is the one
 * rule you can be forced to comply with *today* and no other tool remediates. Lead with it. */
function ShaPinReadiness() {
  const q = useQuery({
    queryKey: ["policy", "sha-pin-readiness"],
    queryFn: () =>
      api.simulatePolicy({
        require_sha_pinned: true,
        require_permissions: false,
        disallowed_triggers: [],
      }),
  });
  const rule = q.data?.by_rule.find((r) => r.rule === "require_sha_pinned");
  const unpinned = rule?.workflows ?? 0;
  const repos = rule?.repos ?? 0;

  return (
    <div className={`card shapin-hero${unpinned > 0 ? " at-risk" : " clear"}`}>
      <div className="shapin-badge">
        {unpinned > 0 ? <IconShield size={20} /> : <IconCheck size={20} />}
        <span>Enforceable today</span>
      </div>
      <div className="shapin-body">
        {q.isLoading ? (
          <div className="shapin-headline">Checking SHA-pin readiness…</div>
        ) : unpinned > 0 ? (
          <>
            <div className="shapin-headline">
              <strong>{unpinned}</strong> workflow{unpinned === 1 ? "" : "s"} across{" "}
              <strong>{repos}</strong> repo{repos === 1 ? "" : "s"} use unpinned actions
            </div>
            <p className="shapin-note">
              GitHub's org policy <em>“require actions pinned to a full-length commit SHA”</em> is GA
              (Aug 2025) — turn it on and these fail immediately. Dependabot and Renovate bump tags but
              <strong> can't pin to a SHA</strong>, so this is on you. One campaign fixes the whole fleet.
            </p>
          </>
        ) : (
          <>
            <div className="shapin-headline">Every action in the fleet is SHA-pinned ✓</div>
            <p className="shapin-note">
              You can enable GitHub's “require SHA-pinned actions” org policy without breaking a single
              workflow.
            </p>
          </>
        )}
      </div>
      {unpinned > 0 && rule && (
        <div className="shapin-cta">
          <CampaignButton
            name="Pin all actions to SHAs"
            operation="pin-shas"
            repoIds={rule.fixable_repo_ids}
            label="Pin the fleet"
          />
        </div>
      )}
    </div>
  );
}

export function PolicyTab() {
  const [sha, setSha] = useState(true);
  const [perms, setPerms] = useState(true);
  const [triggers, setTriggers] = useState("pull_request_target");

  const sim = useMutation({
    mutationFn: () =>
      api.simulatePolicy({
        require_sha_pinned: sha,
        require_permissions: perms,
        disallowed_triggers: triggers
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
      }),
  });
  const report = sim.data;

  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Policy readiness</h2>
          <div className="sub">
            Simulate a ruleset against the fleet before you enforce it — see what breaks, then fix it.
          </div>
        </div>
      </div>

      <ShaPinReadiness />

      <div className="card policy-form">
        <label className="policy-check">
          <input type="checkbox" checked={sha} onChange={(e) => setSha(e.target.checked)} />
          Require SHA-pinned actions
        </label>
        <label className="policy-check">
          <input type="checkbox" checked={perms} onChange={(e) => setPerms(e.target.checked)} />
          Require a top-level permissions block
        </label>
        <label className="policy-triggers">
          Disallowed triggers
          <input
            className="settings-input"
            value={triggers}
            onChange={(e) => setTriggers(e.target.value)}
            placeholder="pull_request_target, workflow_run"
          />
        </label>
        <button className="btn primary" onClick={() => sim.mutate()} disabled={sim.isPending}>
          {sim.isPending ? "Simulating…" : "Simulate policy"}
        </button>
      </div>

      {sim.isError && <ErrorBanner error={sim.error} />}

      {report && (
        <>
          <div className="card scorecard policy-summary">
            <div className="scorecard-stats" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
              <div className="stat">
                <span className="stat-label">Workflows evaluated</span>
                <div className="stat-value">{report.workflows_evaluated}</div>
              </div>
              <div className="stat">
                <span className="stat-label">Workflows violating</span>
                <div className={`stat-value${report.workflows_violating > 0 ? " fail" : ""}`}>
                  {report.workflows_violating}
                </div>
              </div>
              <div className="stat">
                <span className="stat-label">Repos affected</span>
                <div className="stat-value">{report.repos_violating}</div>
              </div>
            </div>
          </div>

          {report.by_rule.length === 0 ? (
            <EmptyState icon={<IconShield size={34} />} title="Fleet is already compliant">
              No workflow violates the selected rules — you can enforce this ruleset safely.
            </EmptyState>
          ) : (
            <div className="rule-list">
              {report.by_rule.map((r: RuleImpact) => (
                <div className="card rule-row" key={r.rule}>
                  <div className="rule-main">
                    <div className="rule-name">{RULE_LABEL[r.rule] ?? r.rule}</div>
                    <div className="rule-impact">
                      breaks <strong>{r.workflows}</strong> workflow{r.workflows === 1 ? "" : "s"} in{" "}
                      <strong>{r.repos}</strong> repo{r.repos === 1 ? "" : "s"}
                    </div>
                  </div>
                  <div className="rule-fix">
                    {r.fix_operation ? (
                      <CampaignButton
                        name={`Fix: ${RULE_LABEL[r.rule] ?? r.rule}`}
                        operation={r.fix_operation}
                        repoIds={r.fixable_repo_ids}
                        label="Fix"
                      />
                    ) : (
                      <span className="subtle" style={{ fontSize: 12 }}>
                        manual remediation
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      <div className="policy-divider" />
      <RequireCheckPanel />
    </section>
  );
}
