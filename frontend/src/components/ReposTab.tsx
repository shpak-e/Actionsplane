import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useHasOperateToken } from "../lib/auth";
import { EmptyState, ErrorBanner, IconCheck, IconRepo, IconX, TableSkeleton } from "./ui";
import type { Repo } from "../types";

export function ReposTab() {
  const qc = useQueryClient();
  const hasToken = useHasOperateToken();
  const [spec, setSpec] = useState("");

  const { data: repos = [], isLoading, isError, error } = useQuery({
    queryKey: ["allRepos"],
    queryFn: api.allRepos,
  });

  function refresh() {
    for (const k of ["repos", "allRepos", "scorecard"]) qc.invalidateQueries({ queryKey: [k] });
  }

  const add = useMutation({
    mutationFn: (value: string) => {
      const cleaned = value.trim().replace(/^https?:\/\/github\.com\//i, "").replace(/\.git$/, "");
      const [owner, name] = cleaned.split("/");
      if (!owner || !name) throw new Error('Enter a repository as "owner/name".');
      return api.addRepo({ owner, name });
    },
    onSuccess: () => {
      setSpec("");
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.removeRepo(id),
    onSuccess: refresh,
  });

  const watched = repos.filter((r) => r.watched);
  const removed = repos.filter((r) => !r.watched);
  const ordered = [...watched, ...removed];

  return (
    <section>
      <div className="content-head">
        <div>
          <h2>Repositories</h2>
          <div className="sub">
            {watched.length} watched
            {removed.length > 0 && ` · ${removed.length} removed`} in the fleet
          </div>
        </div>
      </div>

      {hasToken ? (
        <form
          className="repo-add"
          onSubmit={(e) => {
            e.preventDefault();
            if (spec.trim()) add.mutate(spec);
          }}
        >
          <IconRepo className="repo-add-icon" />
          <input
            className="settings-input repo-add-input"
            placeholder="owner/name  ·  add a repository to the fleet"
            value={spec}
            onChange={(e) => setSpec(e.target.value)}
            autoComplete="off"
          />
          <button className="btn primary" type="submit" disabled={add.isPending || !spec.trim()}>
            {add.isPending ? "Adding…" : "Add repo"}
          </button>
        </form>
      ) : (
        <div className="error-banner" style={{ marginBottom: 16 }}>
          Set an operate token (key icon, top-right) to add or remove repositories.
        </div>
      )}

      {add.isError && (
        <div className="toast err" style={{ marginBottom: 16 }}>
          <IconX /> {(add.error as Error).message}
        </div>
      )}

      {isError ? (
        <ErrorBanner error={error} />
      ) : isLoading ? (
        <TableSkeleton />
      ) : ordered.length === 0 ? (
        <EmptyState icon={<IconRepo size={34} />} title="No repositories">
          Install the GitHub App on some repos, or add one above by <span className="mono">owner/name</span>.
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Repository</th>
                <th>Default branch</th>
                <th>Status</th>
                <th aria-label="actions" />
              </tr>
            </thead>
            <tbody>
              {ordered.map((r: Repo) => (
                <tr className="row" key={r.id}>
                  <td>
                    <span className="mono" style={{ color: "var(--fg)" }}>
                      <span className="subtle">{r.owner}/</span>
                      {r.name}
                    </span>
                  </td>
                  <td className="mono muted">{r.default_branch}</td>
                  <td>
                    {r.watched ? (
                      <span className="badge ok">
                        <IconCheck /> watched
                      </span>
                    ) : (
                      <span className="badge neutral">removed</span>
                    )}
                  </td>
                  <td className="actions">
                    {hasToken &&
                      (r.watched ? (
                        <button
                          className="btn sm"
                          onClick={() => remove.mutate(r.id)}
                          disabled={remove.isPending}
                          title="Remove from the watched fleet"
                        >
                          Remove
                        </button>
                      ) : (
                        <button
                          className="btn sm primary"
                          onClick={() => add.mutate(`${r.owner}/${r.name}`)}
                          disabled={add.isPending}
                          title="Re-add to the watched fleet"
                        >
                          Re-add
                        </button>
                      ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
