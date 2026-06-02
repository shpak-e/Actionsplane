import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { Repo } from "../types";

/** Repos plus an id→repo lookup, so any view can resolve owner/name from a run's repo_id. */
export function useRepos() {
  const query = useQuery({ queryKey: ["repos"], queryFn: api.repos });
  const repos: Repo[] = query.data ?? [];
  const byId = new Map<number, Repo>(repos.map((r) => [r.id, r]));
  return { ...query, repos, byId };
}
