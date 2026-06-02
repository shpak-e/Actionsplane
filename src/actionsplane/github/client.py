"""Thin GitHub REST client (plan §4).

Wraps httpx with installation-token auth and the headers GitHub expects. Only the calls the
control plane actually needs live here; GraphQL (for batch cross-repo reads) is layered on
later. Used by the reconciliation loop to replay runs the webhooks may have missed.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from actionsplane.config import get_settings

log = logging.getLogger(__name__)

_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"

# RFC 5988 Link header: <https://api.github.com/...?page=2>; rel="next", <...>; rel="last"
_NEXT_LINK_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class GitHubClient:
    """Authenticated client for one installation token."""

    def __init__(
        self, token: str | None, *, client: httpx.AsyncClient, api_url: str | None = None
    ) -> None:
        self._client = client
        # token may be None in offline mode (unauthenticated public reads, lower rate limit).
        self._token = token
        self._base = (api_url or get_settings().github_api_url).rstrip("/")
        # url -> (etag, parsed-json body). Lets steady-state sweeps reuse cached responses via
        # conditional requests (304 Not Modified is free under GitHub's primary rate limit).
        self._etag_cache: dict[str, tuple[str, object]] = {}

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": _ACCEPT, "X-GitHub-Api-Version": _API_VERSION}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def get_repo_meta(self, owner: str, repo: str) -> dict[str, Any]:
        """Fetch a repo's metadata (id, default_branch, archived) — needed to upsert the row
        in offline mode where there's no installation webhook to supply it."""
        body = await self._get_json(f"{self._base}/repos/{owner}/{repo}")
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _cache_key(url: str, params: dict | None) -> str:
        """ETag cache key. Includes the query string so paginated pages don't collide on one URL."""
        if not params:
            return url
        return f"{url}?{urlencode(sorted(params.items()))}"

    async def _get_cached(
        self, url: str, *, params: dict | None = None
    ) -> tuple[object, httpx.Headers]:
        """GET with ETag caching + one Retry-After retry; returns (parsed-body, response-headers).

        Conditional-request 304 returns the previously-cached body (the live 304's headers still
        carry pagination Link). Secondary rate limits return 429/403 with a ``Retry-After`` header
        (seconds); we sleep and retry once. Primary rate limits are surfaced to the caller's pacing.
        """
        key = self._cache_key(url, params)
        cached = self._etag_cache.get(key)
        headers = dict(self._headers)
        if cached is not None:
            headers["If-None-Match"] = cached[0]
        for attempt in (0, 1):
            resp = await self._client.get(url, headers=headers, params=params)
            if resp.status_code in (429, 403) and resp.headers.get("retry-after") and attempt == 0:
                await asyncio.sleep(min(float(resp.headers["retry-after"]), 60))
                continue
            break
        if resp.status_code == 304 and cached is not None:
            return cached[1], resp.headers
        resp.raise_for_status()
        body = resp.json()
        etag = resp.headers.get("etag")
        if etag:
            self._etag_cache[key] = (etag, body)
        return body, resp.headers

    async def _get_json(self, url: str, *, params: dict | None = None) -> object:
        """GET a single resource (ETag-cached, Retry-After-aware)."""
        body, _ = await self._get_cached(url, params=params)
        return body

    async def _get_paginated(
        self, url: str, *, params: dict | None = None, max_pages: int = 20
    ) -> AsyncIterator[object]:
        """Yield each page's parsed body, following the ``rel="next"`` Link header.

        Each page is ETag-cached independently (the next-link URL embeds its own ``page`` query).
        Bounded by ``max_pages`` so a pathological repo can't make a sweep walk unboundedly; the
        caller logs when it stops early. Harmless on non-paginated endpoints (one page, no Link).
        """
        next_url: str | None = url
        next_params = params
        pages = 0
        while next_url and pages < max_pages:
            body, resp_headers = await self._get_cached(next_url, params=next_params)
            yield body
            pages += 1
            match = _NEXT_LINK_RE.search(resp_headers.get("link", ""))
            next_url = match.group(1) if match else None
            next_params = None  # the next-link URL already carries page/per_page
        if next_url:
            log.warning("pagination hit max_pages=%d for %s (more pages exist)", max_pages, url)

    async def list_workflow_runs(
        self, owner: str, repo: str, *, per_page: int = 100, max_runs: int = 500
    ) -> list[dict[str, Any]]:
        """Recent workflow runs (newest first), walking pages up to ``max_runs``.

        ETag-cached + Retry-After-aware per page. ``max_runs`` bounds memory/time for a busy repo;
        runs come newest-first, so the cap keeps the most recent. Truncation is logged, not silent.
        """
        runs: list[dict[str, Any]] = []
        async for page in self._get_paginated(
            f"{self._base}/repos/{owner}/{repo}/actions/runs",
            params={"per_page": min(per_page, 100)},
        ):
            if isinstance(page, dict):
                runs.extend(page.get("workflow_runs", []))
            if len(runs) >= max_runs:
                log.info("capping %s/%s runs at max_runs=%d", owner, repo, max_runs)
                return runs[:max_runs]
        return runs

    async def list_workflow_files(self, owner: str, repo: str) -> list[str]:
        """Paths of every workflow YAML. Paginated (Link-walked) + ETag-cached.

        The contents API returns up to 1,000 entries and historically does not page, but walking
        ``rel="next"`` is forward-safe and a no-op for the single-page case.
        """
        url = f"{self._base}/repos/{owner}/{repo}/contents/.github/workflows"
        out: list[str] = []
        try:
            async for page in self._get_paginated(url):
                out.extend(
                    item["path"]
                    for item in (page if isinstance(page, list) else [])
                    if item.get("type") == "file" and item["name"].endswith((".yml", ".yaml"))
                )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []  # repo has no workflows dir
            raise
        return out

    async def get_file_text(self, owner: str, repo: str, path: str) -> str:
        """Decoded text content of a file via the contents API (base64 payload)."""
        if ".." in path.split("/"):
            raise ValueError(f"refusing path traversal in {path!r}")
        safe_path = quote(path)  # keep "/" but encode the rest, preventing URL injection
        resp = await self._client.get(
            f"{self._base}/repos/{owner}/{repo}/contents/{safe_path}",
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return base64.b64decode(data["content"]).decode("utf-8")

    async def get_file(self, owner: str, repo: str, path: str, *, ref: str | None = None) -> dict:
        """Fetch a file's decoded text + blob sha (the sha is needed to update it via PUT)."""
        if ".." in path.split("/"):
            raise ValueError(f"refusing path traversal in {path!r}")
        params = {"ref": ref} if ref else None
        resp = await self._client.get(
            f"{self._base}/repos/{owner}/{repo}/contents/{quote(path)}",
            headers=self._headers,
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"text": base64.b64decode(data["content"]).decode("utf-8"), "sha": data["sha"]}

    async def get_commit_sha(self, owner: str, repo: str, ref: str) -> str:
        """Resolve a tag/branch/ref to its full commit SHA (for pin-to-SHA)."""
        resp = await self._client.get(
            f"{self._base}/repos/{owner}/{repo}/commits/{quote(ref)}",
            headers={**self._headers, "Accept": "application/vnd.github.sha"},
        )
        resp.raise_for_status()
        return resp.text.strip()

    async def get_ref_sha(self, owner: str, repo: str, branch: str) -> str:
        """SHA the head of a branch points at (base for a new branch)."""
        resp = await self._client.get(
            f"{self._base}/repos/{owner}/{repo}/git/ref/heads/{quote(branch)}",
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()["object"]["sha"]

    async def create_branch(self, owner: str, repo: str, branch: str, base_sha: str) -> None:
        """Create a new branch ref at base_sha."""
        resp = await self._client.post(
            f"{self._base}/repos/{owner}/{repo}/git/refs",
            headers=self._headers,
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        resp.raise_for_status()

    async def put_file(
        self,
        owner: str,
        repo: str,
        path: str,
        *,
        text: str,
        message: str,
        branch: str,
        sha: str,
    ) -> None:
        """Commit a file change on a branch (PUT contents; sha = existing blob sha)."""
        resp = await self._client.put(
            f"{self._base}/repos/{owner}/{repo}/contents/{quote(path)}",
            headers=self._headers,
            json={
                "message": message,
                "content": base64.b64encode(text.encode()).decode(),
                "branch": branch,
                "sha": sha,
            },
        )
        resp.raise_for_status()

    async def create_pull_request(
        self, owner: str, repo: str, *, head: str, base: str, title: str, body: str
    ) -> dict:
        """Open a PR; returns {"number", "html_url"}."""
        resp = await self._client.post(
            f"{self._base}/repos/{owner}/{repo}/pulls",
            headers=self._headers,
            json={"head": head, "base": base, "title": title, "body": body},
        )
        resp.raise_for_status()
        data = resp.json()
        return {"number": data["number"], "html_url": data["html_url"]}

    async def rerun_run(self, owner: str, repo: str, run_id: int) -> None:
        """Re-run all jobs of a workflow run. Requires the ``actions: write`` permission.

        GitHub returns 201 with an empty body on success; we just surface a non-2xx as an error.
        """
        resp = await self._client.post(
            f"{self._base}/repos/{owner}/{repo}/actions/runs/{run_id}/rerun",
            headers=self._headers,
        )
        resp.raise_for_status()

    async def upload_sarif(
        self,
        owner: str,
        repo: str,
        sarif: dict,
        *,
        commit_sha: str,
        ref: str,
    ) -> dict:
        """Upload a SARIF document to Code Scanning. Requires ``security_events: write`` scope.

        GitHub expects the SARIF blob gzipped + base64-encoded under the ``sarif`` key. Returns
        ``{id, url}`` for the analysis upload, which the caller stores so a follow-up audit can
        update the same set of alerts via ``partialFingerprints``.
        """
        encoded = base64.b64encode(gzip.compress(json.dumps(sarif).encode())).decode()
        resp = await self._client.post(
            f"{self._base}/repos/{owner}/{repo}/code-scanning/sarifs",
            headers=self._headers,
            json={
                "commit_sha": commit_sha,
                "ref": ref,
                "sarif": encoded,
                "tool_name": "actionsplane",
            },
        )
        resp.raise_for_status()
        return resp.json()
