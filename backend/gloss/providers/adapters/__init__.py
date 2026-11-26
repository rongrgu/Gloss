"""CLI adapters by name (DESIGN.md §6)."""

from .claude import ClaudeAdapter
from .codex import CodexAdapter

ADAPTERS = {"claude": ClaudeAdapter, "codex": CodexAdapter}

__all__ = ["ADAPTERS", "ClaudeAdapter", "CodexAdapter"]
