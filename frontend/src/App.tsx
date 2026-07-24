import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { NavRail, type RailTab } from "./components/NavRail";
import { RepoTree, type WorkflowSel } from "./components/RepoTree";
import { DashboardTab } from "./components/DashboardTab";
import { RunGrid } from "./components/RunGrid";
import { RunDetail } from "./components/RunDetail";
import { SecurityTab } from "./components/SecurityTab";
import { DriftTab } from "./components/DriftTab";
import { PipelinesTab } from "./components/PipelinesTab";
import { PolicyTab } from "./components/PolicyTab";
import { RadarTab } from "./components/RadarTab";
import { CampaignsTab } from "./components/CampaignsTab";
import { SettingsTab } from "./components/SettingsTab";
import { DocsTab } from "./components/DocsTab";
import { SyncButton } from "./components/SyncButton";
import { SettingsMenu } from "./components/SettingsMenu";
import {
  BrandCampaigns,
  BrandDrift,
  BrandHome,
  BrandPipelines,
  BrandPolicy,
  BrandRadar,
  BrandDocs,
  BrandRuns,
  BrandSecurity,
  BrandSettings,
  IconKey,
  IconRepo,
  IconX,
} from "./components/ui";
import { useEventStream } from "./hooks/useEventStream";
import { useMode } from "./hooks/useMode";
import { useRepos } from "./hooks/useRepos";
import { useTheme } from "./hooks/useTheme";
import { promptForToken, useHasOperateToken } from "./lib/auth";
import type { Run } from "./types";

type Tab =
  | "home"
  | "runs"
  | "security"
  | "policy"
  | "radar"
  | "drift"
  | "pipelines"
  | "campaigns"
  | "settings"
  | "docs";

export default function App() {
  const [repoId, setRepoId] = useState<number | null>(null);
  const [workflow, setWorkflow] = useState<WorkflowSel>(null);
  const [tab, setTab] = useState<Tab>("home");
  const [selectedRun, setSelectedRun] = useState<Run | null>(null);
  const [firstRunDismissed, setFirstRunDismissed] = useState(false);
  const { connected } = useEventStream();
  const { data: mode } = useMode();
  const { theme, toggle } = useTheme();
  const hasToken = useHasOperateToken();
  const { repos, byId } = useRepos();

  // Cached (shared with the tabs) — drive the attention badges on the rail.
  const { data: scorecard } = useQuery({ queryKey: ["scorecard"], queryFn: api.scorecard });
  const { data: driftBindings } = useQuery({ queryKey: ["drift", null], queryFn: () => api.drift() });
  const { data: radar } = useQuery({ queryKey: ["radar"], queryFn: api.radar });

  const driftCount = (driftBindings ?? []).filter(
    (b) => b.drift_severity && !["identical", "minor"].includes(b.drift_severity),
  ).length;
  const radarCount = (radar?.entries ?? []).filter(
    (e) => e.status === "overdue" || e.status === "urgent",
  ).length;

  const tabs: RailTab<Tab>[] = [
    {
      id: "home",
      label: "Home",
      icon: <BrandHome size={22} />,
      hint: "Your customizable fleet overview",
    },
    {
      id: "runs",
      label: "Runs",
      icon: <BrandRuns size={22} />,
      group: "Observe",
      hint: "Live workflow-run history across the fleet",
    },
    {
      id: "pipelines",
      label: "Pipelines",
      icon: <BrandPipelines size={22} />,
      group: "Observe",
      hint: "How workflows trigger and call each other across repos",
    },
    {
      id: "security",
      label: "Security",
      icon: <BrandSecurity size={22} />,
      group: "Audit",
      hint: "Supply-chain & hygiene findings, with one-click fixes",
      count: scorecard?.open_findings,
    },
    {
      id: "drift",
      label: "Drift",
      icon: <BrandDrift size={22} />,
      group: "Audit",
      hint: "How far a workflow has diverged from its canonical template",
      count: driftCount,
    },
    {
      id: "radar",
      label: "Radar",
      icon: <BrandRadar size={22} />,
      group: "Audit",
      hint: "Upcoming GitHub deprecations you must migrate before a deadline",
      count: radarCount,
    },
    {
      id: "policy",
      label: "Policy",
      icon: <BrandPolicy size={22} />,
      group: "Audit",
      hint: "Simulate a ruleset against the fleet before you enforce it",
    },
    {
      id: "campaigns",
      label: "Campaigns",
      icon: <BrandCampaigns size={22} />,
      group: "Act",
      hint: "Fleet-wide edit waves — dry-run diffs, then reviewed PRs",
    },
    {
      id: "settings",
      label: "Settings",
      icon: <BrandSettings size={22} />,
      group: "Manage",
      hint: "Notifications, repositories, and access control",
    },
    {
      id: "docs",
      label: "Docs",
      icon: <BrandDocs size={22} />,
      group: "Manage",
      hint: "Usage guide and API reference",
    },
  ];

  // Selecting a repo/workflow updates the scope of whatever tab you're on — it never yanks you to
  // another tab (the workflow filter applies to Runs; on Security/Drift it scopes by the repo).
  function selectRepo(id: number | null) {
    setRepoId(id);
    setWorkflow(null);
  }
  function selectWorkflow(wfRepoId: number, wf: WorkflowSel) {
    setRepoId(wfRepoId);
    setWorkflow(wf);
  }

  // The repo tree only scopes the repo-centric tabs; the fleet-wide tabs (Radar, Policy,
  // Pipelines, Campaigns, Repos) ignore a selection, so we hide it there rather than show an
  // always-on panel that does nothing on those views.
  const showTree = tab === "runs" || tab === "security" || tab === "drift";
  const scopedRepo = showTree && repoId != null ? byId.get(repoId) : undefined;
  const scopeName = scopedRepo ? `${scopedRepo.owner}/${scopedRepo.name}` : null;

  // First-run nudge: writes are gated on an operate token, and the fleet is empty until repos are
  // added. Rather than leave every Fix button disabled with a dead tooltip, point the way.
  const showFirstRun = !firstRunDismissed && (!hasToken || repos.length === 0);

  return (
    <div className="app">
      <NavRail tabs={tabs} active={tab} onSelect={setTab} theme={theme} onToggleTheme={toggle} />

      <div className="workspace">
        <header className="topbar">
          <div className="brand">
            <div>
              <h1>actionsplane</h1>
              <div className="tagline">Observe · Audit · Fix — Actions fleet</div>
            </div>
          </div>

          {scopeName && (
            <span className="scope-chip" title="This view is filtered to one repository">
              <IconRepo size={12} /> {scopeName}
              <button
                onClick={() => selectRepo(null)}
                aria-label="Clear repository scope"
                title="Clear scope — show the whole fleet"
              >
                <IconX size={11} />
              </button>
            </span>
          )}

          <div className="topbar-spacer" />

          {mode?.offline ? (
            <SyncButton syncedAt={mode.synced_at} />
          ) : (
            <span
              className={connected ? "live on" : "live"}
              title={
                connected
                  ? "Live — receiving run updates as they happen"
                  : "Reconnecting to the live update stream (data still loads on refresh)"
              }
            >
              <span className="dot" />
              {connected ? "Live" : "Reconnecting…"}
            </span>
          )}

          <SettingsMenu />
        </header>

        {showFirstRun && (
          <div className="firstrun">
            <span className="firstrun-msg">
              {repos.length === 0
                ? "Add repositories from the Repos tab, then "
                : "To open fix PRs and run campaigns, "}
              set an operate token to enable the write controls.
            </span>
            <button className="btn sm primary" onClick={promptForToken}>
              <IconKey size={13} /> Set operate token
            </button>
            <button
              className="firstrun-close"
              onClick={() => setFirstRunDismissed(true)}
              aria-label="Dismiss"
              title="Dismiss"
            >
              <IconX size={13} />
            </button>
          </div>
        )}

        <div className={showTree ? "body" : "body no-tree"}>
          {showTree && (
            <RepoTree
              selectedRepoId={repoId}
              selectedWorkflow={workflow}
              onSelectRepo={selectRepo}
              onSelectWorkflow={selectWorkflow}
            />
          )}
          <main className="content">
            {tab === "home" && <DashboardTab onNavigate={(t) => setTab(t as Tab)} />}
            {tab === "runs" && (
              <RunGrid
                repoId={repoId}
                workflow={workflow}
                onClearWorkflow={() => setWorkflow(null)}
                onSelect={setSelectedRun}
                selectedRunId={selectedRun?.id ?? null}
              />
            )}
            {tab === "security" && <SecurityTab repoId={repoId} onSelectRepo={selectRepo} />}
            {tab === "policy" && <PolicyTab />}
            {tab === "radar" && <RadarTab />}
            {tab === "drift" && <DriftTab repoId={repoId} />}
            {tab === "pipelines" && <PipelinesTab />}
            {tab === "campaigns" && <CampaignsTab />}
            {tab === "settings" && <SettingsTab />}
            {tab === "docs" && <DocsTab />}
          </main>
        </div>
      </div>

      {selectedRun && <RunDetail run={selectedRun} onClose={() => setSelectedRun(null)} />}
    </div>
  );
}
