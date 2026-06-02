import { useRepos } from "../hooks/useRepos";
import { IconRepo } from "./ui";

export function RepoList({
  selected,
  onSelect,
}: {
  selected: number | null;
  onSelect: (repoId: number | null) => void;
}) {
  const { repos, isLoading } = useRepos();

  return (
    <aside className="sidebar">
      <div className="sidebar-title">Repositories</div>

      <button
        className={selected === null ? "repo active" : "repo"}
        onClick={() => onSelect(null)}
      >
        <IconRepo className="repo-icon" />
        <span className="repo-name">All repositories</span>
      </button>

      {isLoading && <div className="skeleton skeleton-row" style={{ width: "80%" }} />}

      {!isLoading && repos.length === 0 && (
        <p className="muted" style={{ padding: "8px 10px", fontSize: 12 }}>
          No repositories yet. Install the GitHub App (or run the seed script).
        </p>
      )}

      {repos.map((r) => (
        <button
          key={r.id}
          className={selected === r.id ? "repo active" : "repo"}
          onClick={() => onSelect(r.id)}
          title={`${r.owner}/${r.name}`}
        >
          <IconRepo className="repo-icon" />
          <span className="repo-name">
            <span className="repo-owner">{r.owner}/</span>
            {r.name}
          </span>
        </button>
      ))}
    </aside>
  );
}
