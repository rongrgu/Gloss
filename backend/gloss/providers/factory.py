"""Pick the LLMProvider from config (DESIGN.md §6)."""

from __future__ import annotations

from ..config import Config
from .base import LLMProvider
from .cli_runner import CLIProvider

PROVIDERS = ("claude", "codex", "anthropic_api", "openai_api")
ALIASES = {"claude_cli": "claude"}  # the name before the CLI adapters
CLAUDE_ALIASES = {"haiku", "sonnet", "opus", "fable"}


def non_claude(model: str | None) -> str | None:
    """codex has its own default model; a Claude alias left over from the default config means "no choice"."""
    return None if not model or model.strip().lower() in CLAUDE_ALIASES else model


def provider_name(config: Config) -> str:
    return ALIASES.get(config.provider, config.provider)


def make_provider(config: Config) -> LLMProvider:
    name = provider_name(config)
    if name == "claude":
        from .adapters.claude import ClaudeAdapter
        return CLIProvider(ClaudeAdapter(config.claude_bin), config.workspace, model=config.model, timeout=config.timeout)
    if name == "codex":
        from .adapters.codex import CodexAdapter
        return CLIProvider(CodexAdapter(config.codex_bin, use_api_key=config.codex_auth == "api_key"),
                           config.workspace, model=non_claude(config.model), timeout=config.timeout)
    if name == "anthropic_api":
        from .anthropic_api import AnthropicAPIProvider
        return AnthropicAPIProvider(config.model, timeout=config.timeout)
    if name == "openai_api":
        from .openai_api import OpenAIAPIProvider
        return OpenAIAPIProvider(config.model, timeout=config.timeout)
    raise ValueError(f"unknown provider {config.provider!r} ({' | '.join(PROVIDERS)})")
