import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../api";
import { CampaignButton } from "./CampaignButton";
import { EmptyState, ErrorBanner, IconShield } from "./ui";
import type { RuleImpact } from "../types";

const RULE_LABEL: Record<string, string> = {
  require_sha_pinned: "Require SHA-pinned actions",
  require_permissions: "Require a permissions block",
  disallowed_trigger: "Disallow risky triggers",
};

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
                        name={`policy fix: ${r.rule}`}
                        operation={r.fix_operation}
                        repoIds={r.fixable_repo_ids}
                        label={`Fix with ${r.fix_operation}`}
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
    </section>
  );
}
