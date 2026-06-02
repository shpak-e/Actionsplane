// GitHub.com URL builders. ActionsPlane stores the GitHub run/job ids verbatim (they are the
// primary keys), so deep links to the run page, job logs, commit, and branch are pure functions
// of the repo (owner/name) plus those ids — no extra API round-trip needed.

import type { Job, Repo, Run } from "../types";

const HOST = "https://github.com";

export function repoUrl(repo: Repo): string {
  return `${HOST}/${repo.owner}/${repo.name}`;
}

export function runUrl(repo: Repo, run: Run): string {
  return `${repoUrl(repo)}/actions/runs/${run.id}`;
}

/** GitHub renders a job's logs at this path. */
export function jobLogUrl(repo: Repo, run: Run, job: Job): string {
  return `${repoUrl(repo)}/actions/runs/${run.id}/job/${job.id}`;
}

export function commitUrl(repo: Repo, sha: string): string {
  return `${repoUrl(repo)}/commit/${sha}`;
}

export function branchUrl(repo: Repo, branch: string): string {
  return `${repoUrl(repo)}/tree/${branch}`;
}
