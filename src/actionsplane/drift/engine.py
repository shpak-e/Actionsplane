"""Drift detection engine (plan §5.3, Phase 3).

Compares a repo's workflow against a canonical template at the *structural* (AST) level rather
than textually, so reordered keys or comment/whitespace changes don't register as drift, but a
changed action version, an added/removed job, or a changed step sequence does.

Severity ladder (worst wins):
    identical < minor (cosmetic only) < content (values differ) < structural (jobs/steps differ)

Per plan §10 we start with a strict subset (jobs, steps[].uses/run, permissions, concurrency)
and reject exotic constructs (heavy matrix, YAML anchors) from templating until v2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from actionsplane.models.enums import DriftSeverity
from actionsplane.models.workflow import Job, Workflow

_ORDER = {
    DriftSeverity.IDENTICAL: 0,
    DriftSeverity.MINOR: 1,
    DriftSeverity.CONTENT_DRIFT: 2,
    DriftSeverity.STRUCTURAL_DRIFT: 3,
}


@dataclass(frozen=True, slots=True)
class DriftReport:
    severity: DriftSeverity
    changes: list[str] = field(default_factory=list)

    @property
    def is_drifted(self) -> bool:
        return self.severity not in (DriftSeverity.IDENTICAL, DriftSeverity.MINOR)


def _step_signatures(job: Job) -> list[str]:
    """Ordered structural signature of a job's steps: the uses ref or a run marker."""
    sigs: list[str] = []
    for s in job.steps:
        if s.uses:
            sigs.append(f"uses:{s.uses}")
        elif s.run is not None:
            sigs.append("run:")
    return sigs


def diff(canonical: Workflow, candidate: Workflow) -> DriftReport:
    """Structural diff of a candidate workflow against the canonical template."""
    changes: list[str] = []
    worst = DriftSeverity.IDENTICAL

    def bump(sev: DriftSeverity) -> None:
        nonlocal worst
        if _ORDER[sev] > _ORDER[worst]:
            worst = sev

    # --- jobs present/absent: structural ---
    canon_jobs, cand_jobs = set(canonical.jobs), set(candidate.jobs)
    for missing in sorted(canon_jobs - cand_jobs):
        changes.append(f"job `{missing}` missing from candidate")
        bump(DriftSeverity.STRUCTURAL_DRIFT)
    for extra in sorted(cand_jobs - canon_jobs):
        changes.append(f"job `{extra}` added in candidate")
        bump(DriftSeverity.STRUCTURAL_DRIFT)

    # --- per shared job: step sequence (structural) + values (content) ---
    for jid in sorted(canon_jobs & cand_jobs):
        cjob, djob = canonical.jobs[jid], candidate.jobs[jid]
        csig, dsig = _step_signatures(cjob), _step_signatures(djob)
        if [s.split("@")[0] for s in csig] != [s.split("@")[0] for s in dsig]:
            changes.append(f"job `{jid}`: step sequence differs")
            bump(DriftSeverity.STRUCTURAL_DRIFT)
        elif csig != dsig:
            # same steps in same order, but a pinned version/ref differs -> content drift
            for c, d in zip(csig, dsig, strict=False):
                if c != d:
                    changes.append(f"job `{jid}`: `{c}` → `{d}`")
            bump(DriftSeverity.CONTENT_DRIFT)
        if _norm_perms(cjob.permissions) != _norm_perms(djob.permissions):
            changes.append(f"job `{jid}`: permissions differ")
            bump(DriftSeverity.CONTENT_DRIFT)

    # --- workflow-level values (content) ---
    if _norm_perms(canonical.permissions) != _norm_perms(candidate.permissions):
        changes.append("workflow permissions differ")
        bump(DriftSeverity.CONTENT_DRIFT)
    if bool(canonical.concurrency) != bool(candidate.concurrency):
        changes.append("concurrency block presence differs")
        bump(DriftSeverity.CONTENT_DRIFT)
    if (canonical.name or "") != (candidate.name or ""):
        # name-only change is cosmetic
        changes.append("workflow name differs")
        bump(DriftSeverity.MINOR)

    return DriftReport(severity=worst, changes=changes)


def _norm_perms(perms: object) -> object:
    """Normalise a permissions value so equal-but-differently-shaped blocks compare equal."""
    if perms is None:
        return None
    if isinstance(perms, dict):
        return tuple(sorted((str(k), str(v)) for k, v in perms.items()))
    return str(perms)
