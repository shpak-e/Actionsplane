import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { RepoList } from "./components/RepoList";
import { RunGrid } from "./components/RunGrid";
import { RunDetail } from "./components/RunDetail";
import { SecurityTab } from "./components/SecurityTab";
import { DriftTab } from "./components/DriftTab";
import { PipelinesTab } from "./components/PipelinesTab";
import { SyncButton } from "./components/SyncButton";
import { IconActivity, IconGraph, IconLayers, IconShield } from "./components/ui";
import { useEventStream } from "./hooks/useEventStream";
import { useMode } from "./hooks/useMode";
import type { Run } from "./types";

type Tab = "runs" | "security" | "drift" | "pipelines";

export default function App() {
  const [repoId, setRepoId] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>("runs");
  const [selectedRun, setSelectedRun] = useState<Run | null>(null);
  const { connected } = useEventStream();
  const { data: mode } = useMode();

  // Cached (shared with SecurityTab) — drives the badge on the Security tab.
  const { data: scorecard } = useQuery({ queryKey: ["scorecard"], queryFn: api.scorecard });

  const tabs: Array<{ id: Tab; label: string; icon: ReactNode; count?: number }> = [
    { id: "runs", label: "Runs", icon: <IconActivity /> },
    { id: "security", label: "Security", icon: <IconShield />, count: scorecard?.open_findings },
    { id: "drift", label: "Drift", icon: <IconLayers /> },
    { id: "pipelines", label: "Pipelines", icon: <IconGraph /> },
  ];

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <div className="brand-mark">A</div>
          <div>
            <h1>ActionsPlane</h1>
            <div className="tagline">observe · audit · edit — GitHub Actions across your org</div>
          </div>
        </div>

        <div className="header-spacer" />

        <nav className="segmented" aria-label="Sections">
          {tabs.map((t) => (
            <button
              key={t.id}
              className={tab === t.id ? "seg active" : "seg"}
              onClick={() => setTab(t.id)}
              aria-current={tab === t.id}
            >
              {t.icon}
              {t.label}
              {t.count != null && t.count > 0 && <span className="seg-count">{t.count}</span>}
            </button>
          ))}
        </nav>

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
      </header>

      <div className="body">
        <RepoList selected={repoId} onSelect={setRepoId} />
        <main className="content">
          {tab === "runs" && (
            <RunGrid
              repoId={repoId}
              onSelect={setSelectedRun}
              selectedRunId={selectedRun?.id ?? null}
            />
          )}
          {tab === "security" && <SecurityTab repoId={repoId} />}
          {tab === "drift" && <DriftTab repoId={repoId} />}
          {tab === "pipelines" && <PipelinesTab />}
        </main>
      </div>

      {selectedRun && <RunDetail run={selectedRun} onClose={() => setSelectedRun(null)} />}
    </div>
  );
}
