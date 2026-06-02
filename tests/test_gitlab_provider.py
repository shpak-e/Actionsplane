"""Tests for the GitLab CI provider: parser + include pin audit."""

from __future__ import annotations

from actionsplane.models.enums import PinState, Severity
from actionsplane.providers.base import Provider
from actionsplane.providers.gitlab import (
    GitLabProvider,
    audit_pipeline,
    classify_include,
    parse_gitlab_ci,
)
from actionsplane.providers.gitlab.parser import GitLabInclude

SHA = "a" * 40

PIPELINE = f"""
stages: [build, test]
variables:
  FOO: bar
include:
  - local: '/templates/common.yml'
  - template: 'Security/SAST.gitlab-ci.yml'
  - project: 'group/shared'
    file: '/ci.yml'
    ref: main
  - project: 'group/pinned'
    file: '/ci.yml'
    ref: {SHA}
  - component: $CI_SERVER_FQDN/group/sec/scan@1.2.3
  - component: $CI_SERVER_FQDN/group/deploy/run@{SHA}
  - remote: 'https://example.com/ci.yml'
build-job:
  stage: build
  script: [make]
.hidden-template:
  script: [noop]
"""


def test_provider_satisfies_protocol():
    p = GitLabProvider()
    assert isinstance(p, Provider)
    assert p.name == "gitlab"
    assert p.pipeline_glob() == ".gitlab-ci.yml"


def test_parse_jobs_excludes_reserved_and_hidden():
    pipe = parse_gitlab_ci(PIPELINE)
    assert pipe.jobs == ["build-job"]  # not stages/variables/include, not .hidden-template
    assert len(pipe.includes) == 7


def test_classify_include_states():
    assert classify_include(GitLabInclude({}, "local", "/x.yml")) is PinState.LOCAL
    assert classify_include(GitLabInclude({}, "template", "X")) is PinState.LOCAL
    assert classify_include(GitLabInclude({}, "remote", "https://x")) is PinState.UNPINNED
    assert classify_include(GitLabInclude({}, "project", "g/p")) is PinState.UNPINNED  # no ref
    assert classify_include(GitLabInclude({}, "project", "g/p", "main")) is PinState.BRANCH_PINNED
    assert classify_include(GitLabInclude({}, "component", "c", "1.2.3")) is PinState.TAG_PINNED
    assert classify_include(GitLabInclude({}, "component", "c", SHA)) is PinState.SHA_PINNED


def test_audit_flags_unsafe_includes():
    findings = audit_pipeline(parse_gitlab_ci(PIPELINE))
    refs = {f.ref: f.severity for f in findings}
    # @main project -> high; @1.2.3 component -> medium; remote -> high
    assert any("group/shared@main" in r and s is Severity.HIGH for r, s in refs.items())
    assert any("sec/scan@1.2.3" in r and s is Severity.MEDIUM for r, s in refs.items())
    assert any("remote:" in r and s is Severity.HIGH for r, s in refs.items())
    # SHA-pinned + local + template are NOT flagged
    assert not any(SHA in (r or "") for r in refs)
    assert not any("local:" in (r or "") for r in refs)


def test_unknown_include_is_flagged():
    from actionsplane.providers.gitlab.parser import GitLabInclude, GitLabPipeline

    inc = GitLabInclude({"weird": "x"}, "unknown")
    pipe = GitLabPipeline(path=".gitlab-ci.yml", includes=[inc])
    findings = audit_pipeline(pipe)
    assert len(findings) == 1
    assert findings[0].severity is Severity.LOW
