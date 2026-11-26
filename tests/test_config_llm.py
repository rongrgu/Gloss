"""config.toml `[llm]` (DESIGN.md §6) and the provider factory."""

import pytest

from gloss.config import Config
from gloss.providers.factory import make_provider, provider_name


def load(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text)
    return Config.load(p)


def test_llm_section_and_models(tmp_path):
    c = load(tmp_path, """
model = "haiku"
digest_model = "old-digest"
[llm]
provider = "codex"
codex_auth = "api_key"
[llm.models]
outline = ""
qa = "gpt-6-sol"
quiz = "q-model"
""")
    assert (c.provider, c.codex_auth) == ("codex", "api_key")
    assert c.models == {"qa": "gpt-6-sol", "quiz": "q-model"}  # empty means "use the default"
    assert c.model_for("qa") == "gpt-6-sol" and c.model_for("outline") is None
    assert c.model_for("digest") == "old-digest" and c.model_for("quiz") == "q-model"  # older key, newer section


@pytest.mark.parametrize("text, msg", [
    ("[llm]\nprovider = 'claude'\napi_key = 'x'\n", "unknown keys in \\[llm\\]"),
    ("[llm.models]\nsummary = 'x'\n", "unknown \\[llm.models\\] keys"),
])
def test_llm_section_rejects_unknown_keys(tmp_path, text, msg):
    with pytest.raises(ValueError, match=msg):
        load(tmp_path, text)


def test_factory(tmp_path, monkeypatch):
    assert provider_name(Config(provider="claude_cli", workspace=tmp_path)) == "claude"
    claude = make_provider(Config(provider="claude_cli", workspace=tmp_path, model="haiku"))
    assert (claude.name, claude.model) == ("claude", "haiku")
    codex = make_provider(Config(provider="codex", workspace=tmp_path))  # model left at the default "sonnet"
    assert (codex.name, codex.model) == ("codex", None) and codex.adapter.use_api_key is False
    assert make_provider(Config(provider="codex", workspace=tmp_path, model="gpt-6-sol")).model == "gpt-6-sol"
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    from gloss.providers.openai_api import DEFAULT_MODEL
    assert make_provider(Config(provider="openai_api", workspace=tmp_path)).model == DEFAULT_MODEL
    with pytest.raises(ValueError, match="unknown provider"):
        make_provider(Config(provider="gemini", workspace=tmp_path))
