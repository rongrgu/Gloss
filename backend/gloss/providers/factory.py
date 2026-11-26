"""Pick the LLMProvider from config (DESIGN.md §6)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from ..config import Config
from .base import Capabilities, ErrorKind, Event, LLMProvider, RunRequest
from .cli_runner import CLIProvider

PROVIDERS = ("claude", "codex", "anthropic_api", "openai_api")
ALIASES = {"claude_cli": "claude"}  # the name before the CLI adapters
CLAUDE_ALIASES = {"haiku", "sonnet", "opus", "fable"}


def non_claude(model: str | None) -> str | None:
    """codex has its own default model; a Claude alias left over from the default config means "no choice"."""
    return None if not model or model.strip().lower() in CLAUDE_ALIASES else model


CLIS = ("claude", "codex")  # detection order; claude is the default while a choice between both is pending
LABELS = {"claude": "Claude Code", "codex": "Codex"}


def provider_name(config: Config) -> str | None:
    """The provider set in config.toml, or None to detect it (§6.2)."""
    return ALIASES.get(config.provider, config.provider) if config.provider else None


def installed_clis(config: Config) -> list[str]:
    from .adapters.claude import find_claude
    from .adapters.codex import find_codex
    found = {"claude": config.claude_bin or find_claude(), "codex": config.codex_bin or find_codex()}
    return [n for n in CLIS if found[n]]


@dataclass
class Choice:
    """Which provider runs, and why: config (set in config.toml) / choice (picked in the app) /
    auto (the only CLI installed) / pending (both installed, not picked yet: claude meanwhile) / none."""
    name: str | None
    source: str
    available: list[str]

    @property
    def needs_choice(self) -> bool:
        return self.source == "pending"


def choose_provider(config: Config, picked: str | None = None) -> Choice:
    configured = provider_name(config)
    available = installed_clis(config)
    if configured:
        return Choice(configured, "config", available)
    if picked in available:
        return Choice(picked, "choice", available)
    if len(available) == 1:
        return Choice(available[0], "auto", available)
    if available:
        return Choice(available[0], "pending", available)
    return Choice(None, "none", available)


class MissingProvider:
    """Neither claude nor codex is installed (and config.toml names no provider): every call says so."""
    name = "none"
    caps = Capabilities(supports_output_schema=True, supports_image_flag=False, supports_append_system_prompt=True,
                        streams_text_deltas=True)
    supports_tools = True
    model = None

    async def run(self, req: RunRequest) -> AsyncIterator[Event]:
        yield Event("error", error_kind=ErrorKind.NOT_INSTALLED,
                    text="Neither Claude Code (`claude`) nor Codex (`codex`) is installed. Install one, log in, "
                         "and restart Gloss.")


def make_provider(config: Config, name: str | None = None) -> LLMProvider:
    name = name or provider_name(config) or choose_provider(config).name
    if name is None:
        return MissingProvider()
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
    raise ValueError(f"unknown provider {name!r} ({' | '.join(PROVIDERS)})")
