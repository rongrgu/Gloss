"""Compatibility: `ClaudeCLIProvider` is now the generic CLI runner with the Claude adapter."""

from __future__ import annotations

from pathlib import Path

from .adapters.claude import BASE_FLAGS, READ_ONLY_TOOLS, ClaudeAdapter, clean_env, find_claude
from .cli_runner import CLIProvider

__all__ = ["BASE_FLAGS", "READ_ONLY_TOOLS", "ClaudeCLIProvider", "clean_env", "find_claude"]


class ClaudeCLIProvider(CLIProvider):
    def __init__(self, workspace: Path, model: str | None = None, claude_bin: str | None = None, timeout: float = 600):
        super().__init__(ClaudeAdapter(claude_bin), workspace, model=model, timeout=timeout)
