"""Offline mode — populate the read model from a fixed list of public GitHub repos."""

from actionsplane.offline.sync import last_sync, parse_repo_spec, sync_offline

__all__ = ["last_sync", "parse_repo_spec", "sync_offline"]
