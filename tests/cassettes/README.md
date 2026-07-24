# Write-path cassette corpus

Sanitized recordings of the real GitHub REST exchanges ActionsPlane makes on the write path,
captured during **Phase 5.1 live validation** (2026-07-24) against a personal-account install of
the `actionsplane-lab-shpak-e` App over the `shpak-e/ap-lab-*` lab repos.

Each file is one request/response pair produced by `actionsplane.github.recorder` (enabled with
`ACTIONSPLANE_RECORD_DIR`, see `deploy/docker-compose.record.yml`). Headers are **allowlist-only**,
so no `Authorization`/token/private-key material is present — verified before commit. Bodies are
verbatim and contain only public data (commit SHAs, the bot committer, GitHub's public PGP
commit-verification signature).

| Cassette | Exchange |
|---|---|
| `get-commit-sha-200` | resolve a tag → commit SHA (pin-to-SHA) |
| `get-file-text-200` | read a workflow file |
| `list-workflow-files-200` / `-304` | list `.github/workflows/` (fresh + ETag revalidation) |
| `get-ref-200` | resolve the default-branch head |
| `list-runs-200` | reconcile poll of a repo's recent runs |
| `create-branch-201` | create the campaign branch |
| `put-file-200` | commit the rewritten workflow |
| `open-pr-201` | open the campaign PR |
| `sarif-upload-202` | push findings to Code Scanning |
| `put-file-403-needs-workflows-perm` | the 403 an App without the **Workflows** permission gets on a workflow-file write (see `.env.example`) |

**Follow-up (post-5.1):** wire these into `tests/test_github_client.py` as offline contract tests
(replay via `httpx.MockTransport`) so the client's write path is covered without a live App.
