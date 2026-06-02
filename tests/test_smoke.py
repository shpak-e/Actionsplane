"""Smoke tests: the package imports and core modules are wired up."""

from __future__ import annotations

import importlib


def test_package_version() -> None:
    import actionsplane

    assert isinstance(actionsplane.__version__, str)


def test_core_modules_import() -> None:
    for mod in (
        "actionsplane.config",
        "actionsplane.models",
        "actionsplane.audit.pins",
        "actionsplane.ingestor.signature",
        "actionsplane.github.app_auth",
    ):
        assert importlib.import_module(mod) is not None
