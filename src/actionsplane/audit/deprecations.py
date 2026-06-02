"""Curated deprecation data for the deprecation scanner (plan §5.2).

Conservative and well-sourced: only majors GitHub has actually deprecated (Node 12/16 runtimes,
artifact actions retired in 2024-2025). Extend as GitHub retires more. The run-script tokens are
the deprecated workflow commands.
"""

from __future__ import annotations

# action name -> set of deprecated major tags (anything <= these is flagged)
DEPRECATED_MAJORS: dict[str, set[str]] = {
    "actions/checkout": {"v1", "v2", "v3"},
    "actions/setup-node": {"v1", "v2", "v3"},
    "actions/setup-python": {"v1", "v2", "v3"},
    "actions/upload-artifact": {"v1", "v2", "v3"},
    "actions/download-artifact": {"v1", "v2", "v3"},
    "actions/cache": {"v1", "v2", "v3"},
}

# deprecated workflow commands that may appear inside `run:` scripts
DEPRECATED_RUN_TOKENS: tuple[str, ...] = ("::set-output", "::save-state", "::set-env", "::add-path")
