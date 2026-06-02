"""GitLab CI provider: `.gitlab-ci.yml` parser + include/component pin audit."""

from actionsplane.providers.gitlab.audit import audit_pipeline, classify_include
from actionsplane.providers.gitlab.parser import GitLabInclude, GitLabPipeline, parse_gitlab_ci


class GitLabProvider:
    """Provider implementation for GitLab CI (satisfies actionsplane.providers.base.Provider)."""

    name = "gitlab"

    def pipeline_glob(self) -> str:
        return ".gitlab-ci.yml"


__all__ = [
    "GitLabInclude",
    "GitLabPipeline",
    "GitLabProvider",
    "audit_pipeline",
    "classify_include",
    "parse_gitlab_ci",
]
