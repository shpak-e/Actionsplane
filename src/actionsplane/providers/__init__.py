"""Provider abstraction — the seam for supporting CI/CD stacks beyond GitHub Actions.

Per `docs/multi-ci-research.md`, GitLab CI is the first additional provider: its webhooks,
`include:`/CI-Components supply-chain surface, and Merge Requests map cleanly onto ActionsPlane's
observe/audit/drift/edit pillars. The two seams that concentrate the risk — the pipeline parser
and the finding taxonomy — are made explicitly provider-pluggable here.
"""

from actionsplane.providers.base import Provider

__all__ = ["Provider"]
