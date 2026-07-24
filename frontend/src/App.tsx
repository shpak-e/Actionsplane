import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { NavRail, type RailTab } from "./components/NavRail";
import { RepoTree, type WorkflowSel } from "./components/RepoTree";
import { RunGrid } from "./components/RunGrid";
import { RunDetail } from "./components/RunDetail";
import { SecurityTab } from "./components/SecurityTab";
import { DriftTab } from "./components/DriftTab";
import { PipelinesTab } from "./components/PipelinesTab";
import { ReposTab } from "./components/ReposTab";
import { SyncButton } from "./components/SyncButton";
import { SettingsMenu } from "./components/SettingsMenu";
import { BrandDrift, BrandPipelines, BrandRepos, BrandRuns, BrandSecurity } from "./components/ui";
import { useEventStream } from "./hooks/useEventStream";
import { useMode } from "./hooks/useMode";
import { useTheme } from "./hooks/useTheme";
import type { Run } from "./types";

type Tab = "runs" | "security" | "drift" | "pipelines" | "repos";

export default function App() {
  const [repoId, setRepoId] = useState<number | null>(null);
  const [workflow, setWorkflow] = useState<WorkflowSel>(null);
  const [tab, setTab] = useState<Tab>("runs");
  const [selectedRun, setSelectedRun] = useState<Run | null>(null);
  const { connected } = useEventStream();
  const { data: mode } = useMode();
  const { theme, toggle } = useTheme();

  // Cached (shared with SecurityTab) — drives the count badge on the Security tab.
  const { data: scorecard } = useQuery({ queryKey: ["scorecard"], queryFn: api.scorecard });

  const tabs: RailTab<Tab>[] = [
    { id: "runs", label: "Runs", icon: <BrandRuns size={22} /> },
    { id: "security", label: "Security", icon: <BrandSecurity size={22} />, count: scorecard?.open_findings },
    { id: "drift", label: "Drift", icon: <BrandDrift size={22} /> },
    { id: "pipelines", label: "Pipelines", icon: <BrandPipelines size={22} /> },
    { id: "repos", label: "Repos", icon: <BrandRepos size={22} /> },
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

          <div className="topbar-spacer" />

          {mode?.offline ? (
            <SyncButton syncedAt={mode.synced_at} />
          ) : (
            <span
              className={connected ? "live on" : "live"}
              title={connected ? "Live updates connected" : "Live updates disconnected"}
            >
              <span className="dot" />
              {connected ? "Live" : "Offline"}
            </span>
          )}

          <SettingsMenu />
        </header>

        <div className="body">
          <RepoTree
            selectedRepoId={repoId}
            selectedWorkflow={workflow}
            onSelectRepo={selectRepo}
            onSelectWorkflow={selectWorkflow}
          />
          <main className="content">
            {tab === "runs" && (
              <RunGrid
                repoId={repoId}
                workflow={workflow}
                onClearWorkflow={() => setWorkflow(null)}
                onSelect={setSelectedRun}
                selectedRunId={selectedRun?.id ?? null}
              />
            )}
            {tab === "security" && <SecurityTab repoId={repoId} />}
            {tab === "drift" && <DriftTab repoId={repoId} />}
            {tab === "pipelines" && <PipelinesTab />}
            {tab === "repos" && <ReposTab />}
          </main>
        </div>
      </div>

      {selectedRun && <RunDetail run={selectedRun} onClose={() => setSelectedRun(null)} />}
    </div>
  );
}
