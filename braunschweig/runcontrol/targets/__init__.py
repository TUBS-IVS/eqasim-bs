"""Factory resolving a TargetConfig to its ExecutionTarget implementation."""
from __future__ import annotations

from ..settings import TargetConfig
from .base import ExecutionTarget
from .local import LocalTarget


def get_target(cfg: TargetConfig) -> ExecutionTarget:
    if cfg.kind == "local":
        return LocalTarget(cfg)
    if cfg.kind == "ssh":
        from .ssh import SshTarget            # imported lazily; added in Task 4
        return SshTarget(cfg)
    raise ValueError(f"unknown target kind '{cfg.kind}' for target '{cfg.name}'")
