import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../api";
import { useHasOperateToken } from "../lib/auth";
import { useRepos } from "../hooks/useRepos";
import { IconCheck, IconX } from "./ui";

/**
 * W7 — require a status check across the fleet. Unlike everything else, this is a *direct* GitHub
 * write (a branch ruleset), not a PR, so it's gated + optional: it needs the App's
 * administration:write permission, and per-repo failures (permission not granted) show inline.
 */
export function RequireCheckPanel() {
  const [check, setCheck] = useState("build");
  const hasToken = useHasOperateToken();
  const { repos } = useRepos();
  const ids = repos.map((r) => r.id);

  const m = useMutation({
    mutationFn: () => api.requireCheck({ check: check.trim(), repo_ids: ids }),
  });
  const res = m.data;
  const ok = res?.results.filter((r) => r.status === "required").length ?? 0;

  return (
    <div className="card require-check">
      <div className="rule-name">Enforce a required check across the fleet</div>
      <div className="rule-impact" style={{ marginBottom: 12 }}>
        Adds a branch ruleset requiring this check on each repo's default branch. A direct GitHub
        write (not a PR) — needs the App's <span className="mono">administration:write</span>{" "}
        permission; repos without it are reported below.
      </div>

      <div className="rc-form">
        <input
          className="settings-input"
          value={check}
          onChange={(e) => setCheck(e.target.value)}
          placeholder="check name (e.g. build)"
        />
        <button
          className="btn primary"
          disabled={!hasToken || !check.trim() || ids.length === 0 || m.isPending}
          onClick={() => m.mutate()}
          title={hasToken ? "" : "Set an operate token first"}
        >
          {m.isPending ? "Applying…" : `Require across ${ids.length} repo${ids.length === 1 ? "" : "s"}`}
        </button>
      </div>

      {!hasToken && <p className="settings-hint">Set an operate token (key icon) to enforce.</p>}
      {m.isError && (
        <div className="toast err">
          <IconX /> {(m.error as Error).message}
        </div>
      )}

      {res && (
        <>
          <div className="rule-impact" style={{ margin: "10px 0 4px" }}>
            Required <strong>{check}</strong> on {ok} of {res.results.length} repos.
          </div>
          <ul className="rc-results">
            {res.results.map((r) => (
              <li key={r.repo_id}>
                <span className="mono ct-name">{r.repo ?? `#${r.repo_id}`}</span>
                {r.status === "required" ? (
                  <span className="ct-ok mono">
                    <IconCheck size={12} /> required
                  </span>
                ) : (
                  <span className="ct-err mono">{r.detail}</span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
